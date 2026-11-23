/* Add Songs (docs/oled-design.md#add-songs, docs/internals.md#add-songs): a playlist's or
 * On-The-Go's Add Songs row opens "Add to <name>": Recently Played, Recently Added, Artists, Albums
 * and Songs. An artist opens with Add All (its song count on the right) on top, then its albums
 * (40 px rows, a 32 px cover, the year), then its songs; an album with Add All, then its tracks
 * (the number in its own column, the length on the right); Songs with a Search row on top, which
 * opens the letter strip (search_strip.c add_songs_search). Artists, Albums and Songs have the A-Z
 * index (az_index.c az_mark) past AZ_MIN_ROWS rows, as the Library's.
 *
 * Each song row shows a mark against the playlist as it was when Add Songs opened: a grey
 * check-circle already in, a green plus-circle added now, a red minus-circle taken out now, none
 * otherwise; Centre adds or takes it out in place (playlists.c playlist_add, playlist_remove) and
 * the wheel stays. The marks key on a song's path and CUE start (as_key), kept in two sets:
 * as.was, read once, and as.now, which each change updates.
 *
 * The page is one sheet painted here (as_paint), not widgets, so a list of any length costs its
 * deque alone: the levels (as.lv) are a stack in the one page, Return pops one, and from the
 * chooser closes it. Rows are AS_ROW high, AS_COVER_ROW for an album's; whole rows show from the
 * status row down to AS_END. */
#include "navigation.h"

extern void coverflow_newest(void *albums);
extern unsigned album_key(void *r);
extern char *art_path(char *out, unsigned key, const char *suffix);

#define AS_ROW 32
#define AS_COVER_ROW 40
#define AS_COVER 32
#define AS_END 224 /* the list's height: down to y 282 on screen */
#define AS_LEVELS 4
#define AS_MAX_KEYS 4096 /* the songs whose marks are kept; past it a song shows none */
#define AS_PX TYPE(20)   /* a row's title: the design's 20 px, AWTK's size */
#define AS_PX_15 TYPE(15)

enum { AS_CHOOSE, AS_PLAYED, AS_ADDED, AS_ARTISTS, AS_ALBUMS, AS_SONGS, AS_ARTIST, AS_ALBUM };
static const char *const as_names[] = { "",        "Recently Played", "Recently Added",
                                        "Artists", "Albums",          "Songs" };

typedef struct {
    int kind, sel, top, lead; /* lead: rows before the list's records (Add All, Search) */
    void *list;               /* the records; an artist's: its songs */
    void *albums;             /* an artist's albums, newest first */
    char name[128];
} as_level;

static struct {
    void *page, *sheet;
    int dest, depth, first[27];
    unsigned *was, *now;
    int nwas, nnow;
    short down_x, down_y, pressed, drag;
    as_level lv[AS_LEVELS];
    char name[128];
} as __attribute__((section(".scratch")));

/* A song's place in the sets: its path's hash and its CUE start. */
static unsigned as_key(void *r) {
    return hash_bytes(fnv(FNV_SEED, P(r, REC_PATH)), (const unsigned char *)r + REC_CUE_START, 4);
}
static int as_in(const unsigned *set, int n, unsigned k) {
    for (int i = 0; i < n; ++i)
        if (set[i] == k) return 1;
    return 0;
}
/* The mark of r: 1 in before and now, 2 added now, 3 taken out now, 0 none (pv_paint's kinds). */
int add_songs_mark(void *r) {
    unsigned k = as_key(r);
    int was = as_in(as.was, as.nwas, k), now = as_in(as.now, as.nnow, k);
    return was && now ? 1 : now ? 2 : was ? 3 : 0;
}

/* r added to or taken out of the target, as its mark says, and the mark changed; a toast says
 * what happened. */
void add_songs_toggle(void *r) {
    unsigned k = as_key(r);
    int n, now = as_in(as.now, as.nnow, k), ok = 0;
    char said[160];
    if (!now) {
        ok = playlist_add(r, as.dest) >= 0;
        if (ok && as.now && as.nnow < AS_MAX_KEYS) as.now[as.nnow++] = k;
        if (ok) playlist_synced(as.dest);
    } else {
        void *songs = staged(playlist_query, (void *)(long)as.dest, &n);
        for (unsigned i = 0; songs && i < deque_size(songs) && !ok; ++i)
            if (as_key(deque_at(songs, i)) == k) ok = playlist_remove(as.dest, deque_at(songs, i));
        if (songs) deque_destroy(songs);
        for (int i = 0; ok && i < as.nnow; ++i)
            if (as.now[i] == k) as.now[i] = as.now[--as.nnow];
    }
    g_delete_flag = 1; /* the playlist's page and Playlists read theirs again */
    tk_snprintf(said, sizeof said,
                !ok                         ? "Could not save playlist"
                : !now                      ? "Added to %s"
                : as_in(as.was, as.nwas, k) ? "Removed"
                                            : "Undone",
                as.name);
    toast(said);
}

static as_level *as_top(void) { return &as.lv[as.depth]; }
static int as_rows(as_level *l) {
    return l->kind == AS_CHOOSE ? 5
                                : l->lead + (l->albums ? (int)deque_size(l->albums) : 0) +
                                      (l->list ? (int)deque_size(l->list) : 0);
}
/* Row i of l: an album row (an artist's albums, or Albums), else a record's or a lead row. */
static void *as_album(as_level *l, int i) {
    int a = l->albums ? (int)deque_size(l->albums) : 0;
    if (l->kind == AS_ALBUMS) return i >= 0 && l->list ? deque_at(l->list, (unsigned)i) : 0;
    return i >= l->lead && i < l->lead + a ? deque_at(l->albums, (unsigned)(i - l->lead)) : 0;
}
static void *as_record(as_level *l, int i) {
    int a = l->albums ? (int)deque_size(l->albums) : 0;
    i -= l->lead + a;
    return l->list && i >= 0 && i < (int)deque_size(l->list) ? deque_at(l->list, (unsigned)i) : 0;
}
static int as_row_h(as_level *l, int i) { return as_album(l, i) ? AS_COVER_ROW : AS_ROW; }

/* The A-Z index's lists: name order, past AZ_MIN_ROWS rows; each letter's first row in as.first. */
static int as_indexed(as_level *l) {
    if ((l->kind != AS_ARTISTS && l->kind != AS_ALBUMS && l->kind != AS_SONGS) || !l->list ||
        deque_size(l->list) <= AZ_MIN_ROWS)
        return 0;
    static const unsigned char field[] = { REC_ARTIST, REC_ALBUM };
    char name[256];
    for (int k = 0; k < 27; ++k) as.first[k] = -1;
    for (unsigned i = 0, n = deque_size(l->list); i < n; ++i) {
        void *r = deque_at(l->list, i);
        int c = az_initial(l->kind == AS_SONGS ? track_name(name, sizeof name, r)
                                               : P(r, field[l->kind - AS_ARTISTS]));
        if (as.first[c] < 0) as.first[c] = (int)i + l->lead;
    }
    return 1;
}

/* The selection in view: top moves so the selected row is whole above AS_END. */
static void as_follow(as_level *l) {
    if (l->sel < l->top) l->top = l->sel;
    for (;;) {
        int y = 0;
        for (int i = l->top; i <= l->sel; ++i) y += as_row_h(l, i);
        if (y <= AS_END || l->top >= l->sel) break;
        ++l->top;
    }
}

static int as_artist_query(void *name) {
    return (I(artist_type, 0) == 1 ? getMusicByAlbumArtist : getMusicBySonger)((const char *)name);
}
static int as_artists(void *unused) {
    return I(artist_type, 0) == 1 ? GetAllAlbumArtist() : getAllArtist();
}
static int as_played(void *unused) { return getAllRecentRecord(); }
static int as_added(void *unused) { return getAllRecentAddRecord(); }
static int as_albums(void *unused) { return getAllAlbum(); }

static void as_drop(as_level *l) {
    if (l->list) deque_destroy(l->list);
    if (l->albums) deque_destroy(l->albums);
    memset(l, 0, sizeof *l);
}

/* A level of kind over the one below, of r (an artist's or an album's row). */
static void as_push(int kind, void *r) {
    int n;
    if (as.depth + 1 >= AS_LEVELS) return;
    as_level *l = &as.lv[++as.depth];
    memset(l, 0, sizeof *l);
    l->kind = kind;
    switch (kind) {
    case AS_PLAYED:
        l->list = staged_music(as_played, 0, &n);
        break;
    case AS_ADDED:
        l->list = staged_music(as_added, 0, &n);
        break;
    case AS_ARTISTS:
        l->list = staged_music(as_artists, 0, &n);
        break;
    case AS_ALBUMS:
        l->list = staged_music(as_albums, 0, &n);
        break;
    case AS_SONGS:
        l->list = staged_music(all_songs, (void *)LIBRARY_MUSIC, &n);
        l->lead = 1; /* Search */
        break;
    case AS_ARTIST:
        tk_snprintf(l->name, sizeof l->name, "%s", (const char *)P(r, REC_ARTIST));
        l->list = staged_music(as_artist_query, l->name, &n);
        l->albums = _create_deque("stSongInfo");
        if (l->albums) {
            deque_init(l->albums);
            for (unsigned i = 0; l->list && i < deque_size(l->list); ++i) {
                void *s = deque_at(l->list, i);
                unsigned j = 0;
                while (j < deque_size(l->albums) && album_cmp(deque_at(l->albums, j), s)) ++j;
                if (j == deque_size(l->albums)) _deque_push_back(l->albums, s);
            }
            coverflow_newest(l->albums);
        }
        l->lead = 1; /* Add All */
        break;
    case AS_ALBUM:
        tk_snprintf(l->name, sizeof l->name, "%s", (const char *)P(r, REC_ALBUM));
        l->list = coverflow_album_tracks(r);
        l->lead = 1;
        break;
    }
    if (!l->name[0]) tk_snprintf(l->name, sizeof l->name, "%s", as_names[kind]);
}

/* Add All: every song of the level not in the target now. */
static void as_add_all(as_level *l) {
    int added = 0;
    char said[64];
    for (unsigned i = 0; l->list && i < deque_size(l->list); ++i) {
        void *r = deque_at(l->list, i);
        unsigned k = as_key(r);
        if (as_in(as.now, as.nnow, k) || playlist_add(r, as.dest) < 0) continue;
        if (as.now && as.nnow < AS_MAX_KEYS) as.now[as.nnow++] = k;
        ++added;
    }
    g_delete_flag = 1;
    if (added) playlist_synced(as.dest);
    if (added) tk_snprintf(said, sizeof said, "Added %d song%s", added, added == 1 ? "" : "s");
    toast(added ? said : "Already added");
}

/* The selected row's action: a chooser's list, an artist's or an album's page, Add All, Search, or
 * a song's add or removal. */
static void as_act(void) {
    as_level *l = as_top();
    void *r;
    if (l->kind == AS_CHOOSE)
        as_push(AS_PLAYED + l->sel, 0);
    else if (l->sel < l->lead && l->kind == AS_SONGS)
        add_songs_search(l->list); /* the strip over this list, held while it is open */
    else if (l->sel < l->lead)
        as_add_all(l);
    else if ((r = as_album(l, l->sel)))
        as_push(AS_ALBUM, r);
    else if ((r = as_record(l, l->sel)) && l->kind == AS_ARTISTS)
        as_push(AS_ARTIST, r);
    else if (r)
        add_songs_toggle(r);
}

static void as_dirty(void) {
    if (as.sheet) widget_invalidate_force(as.sheet, (void *)0);
    if (as.page) bar_title_sync(as.page);
}
/* The status row's title: the chooser's "Add to <name>", else the level's. */
static void as_title(void) {
    void *t = as.page ? widget_lookup(as.page, PAGE_TITLE, 0) : (void *)0;
    char s[160];
    if (as.depth)
        tk_snprintf(s, sizeof s, "%s", as_top()->name);
    else
        tk_snprintf(s, sizeof s, "Add to %s", as.name);
    if (t) widget_set_text_utf8(t, s);
}

/* ringnav's keys before stock's: the wheel moves (a fast spin shows the A-Z letter), Centre acts,
 * Return goes back a level; Play/Pause and the side buttons stay the player's. */
int add_songs_key(void *top, unsigned key) {
    if (!as.page || top != as.page) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    as_level *l = as_top();
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0, n = as_rows(l);
    if (wheel) {
        l->sel += wheel;
        l->sel = l->sel < 0 ? 0 : l->sel >= n ? (n ? n - 1 : 0) : l->sel;
        as_follow(l);
        if (as_indexed(l) && (wheel_speed >= AZ_SPIN_DPS || az_on(as.sheet))) {
            char name[256];
            void *r = as_record(l, l->sel) ?: as_album(l, l->sel);
            static const unsigned char field[] = { REC_ARTIST, REC_ALBUM };
            if (r)
                az_mark(as.sheet,
                        az_initial(l->kind == AS_SONGS ? track_name(name, sizeof name, r)
                                                       : P(r, field[l->kind - AS_ARTISTS])),
                        0, wheel_speed >= AZ_SPIN_DPS);
        }
    } else if (key == KEY_CENTER) {
        st.touch_mode = 0;
        as_act();
    } else if (key == KEY_RETURN) {
        if (!as.depth) {
            navigator_back();
            return 1;
        }
        as_drop(l);
        --as.depth;
    } else
        return 0;
    as_title();
    as_dirty();
    *(volatile signed char *)KEY_TONE_LATCH = 0; /* the next press clicks */
    return 1;
}

/* A cover's file:// url for album row r in url (520 bytes), or 0 when Cover Flow has none yet
 * (library_artist.c la_cover's). */
static const char *as_cover(void *r, char *url) {
    tk_snprintf(url, 8, "file://");
    if (catalogue_cover(P(r, REC_PATH), url + 7, 513)) return url;
    art_path(url + 7, album_key(r), "");
    return file_size(url + 7) > 0 ? url : (const char *)0;
}

/* One row at y of height h: its selection bar, cover, number, title, right-hand text, mark and
 * chevron, in final colours. */
static void as_row(void *canvas, as_level *l, int i, int y, int h, int on) {
    int light = ipod_light(), x = geom_val(V_TEXT_X), end = 375 - az_right(as.sheet), tx = x;
    unsigned ink = on      ? 0xffffff
                   : light ? 0
                           : 0xffffff,
             muted = on ? 0xffffff : tok(TOK_TERTIARY), accent = scheme()->tones[0] & 0xffffff;
    char text[256], right[48] = "", url[520], art[32];
    const char *title = text, *icon = 0;
    int chev = 0, mark = 0, num = 0;
    void *r = 0, *album = as_album(l, i);
    if (on) sel_bar_flat(canvas, 0, y, 375 - az_bar_in(as.sheet), h);
    if (l->kind == AS_CHOOSE) {
        title = as_names[AS_PLAYED + i], chev = 1;
    } else if (i < l->lead) {
        int songs = l->list ? (int)deque_size(l->list) : 0;
        icon = l->kind == AS_SONGS ? "search" : "add";
        title = l->kind == AS_SONGS ? "Search" : "Add All";
        if (l->kind != AS_SONGS)
            tk_snprintf(right, sizeof right, "%d song%s", songs, songs == 1 ? "" : "s");
    } else if (album) {
        const char *name = P(album, REC_ALBUM), *cover = as_cover(album, url);
        int year = I(album, REC_YEAR);
        title = name && *name ? name : "Unknown Album", chev = 1;
        if (l->kind == AS_ALBUMS)
            tk_snprintf(right, sizeof right, "%s", (const char *)P(album, REC_ARTIST));
        else if (year > 0)
            tk_snprintf(right, sizeof right, "%d", year);
        rect_t box = { x - 2, y + (h - AS_COVER) / 2, AS_COVER, AS_COVER };
        unsigned bitmap[64];
        if (cover && !widget_load_image(as.sheet, cover, bitmap)) {
            rect_t src = { 0, 0, (int)bitmap[0], (int)bitmap[1] };
            canvas_draw_image(canvas, bitmap, &src, &box);
        } else
            fill_box(canvas, &box, RGBA(tok(TOK_RAISED_2)), 4);
        tx = x + AS_COVER + 8;
    } else if ((r = as_record(l, i))) {
        if (l->kind == AS_ARTISTS) {
            const char *a = P(r, REC_ARTIST);
            title = a && *a && I(r, REC_ID) >= 0 ? a : "Unknown Artist", chev = 1;
        } else {
            title = track_name(text, sizeof text, r);
            mark = add_songs_mark(r);
            if (l->kind == AS_ALBUM) {
                num = I(r, REC_TRACK);
                duration_text(right, sizeof right, r);
                tx = x + 26;
            } else
                tk_snprintf(right, sizeof right, "%s", (const char *)P(r, REC_ARTIST));
        }
    }
    if (icon) {
        tk_snprintf(art, sizeof art, "plact_%s_%s", icon, on ? "w" : "a");
        paint_art(as.sheet, canvas, theme_art(art), x + 2, y + (h - 18) / 2);
        tx = x + 30, ink = on ? 0xffffff : accent;
    }
    if (chev) {
        chevron_icon(as.sheet, canvas, end, y + h / 2, on);
        end -= 20;
    }
    if (mark) {
        static const char *const kinds[] = { "in", "add", "out" };
        tk_snprintf(art, sizeof art, "plmark_%s_%s", kinds[mark - 1], on ? "w" : light ? "l" : "d");
        paint_art(as.sheet, canvas, theme_art(art), end - 21, y + (h - 20) / 2);
        end -= 22 + 8;
    }
    if (num > 0) {
        char d[8];
        tk_snprintf(d, sizeof d, "%d", num);
        ui_text(canvas, d, 0, AS_PX_15, muted, 1, x + 18, y + h / 2, 18, 3);
    }
    if (*right) end -= ui_text(canvas, right, 0, AS_PX_15, muted, 1, end, y + h / 2, 150, 3) + 8;
    ui_text(canvas, title, 0, AS_PX, ink, 1, tx, y + h / 2, end - tx, 2);
}

/* ringnav_paint: the level's rows in view, on the sheet. */
void add_songs_paint(void *w, void *canvas) {
    if (!as.page || w != as.sheet || !P(canvas, CANVAS_LCD)) return;
    as_level *l = as_top();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int raw = ui_ink_raw(1), n = as_rows(l);
    for (int i = l->top, y = 0; i < n; ++i) {
        int h = as_row_h(l, i);
        if (y + h > AS_END) break;
        as_row(canvas, l, i, y, h, i == l->sel && !st.touch_mode);
        y += h;
    }
    ui_ink_raw((unsigned)raw);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Touch: a tap on a row selects and acts on it; a drag up or down scrolls a row per AS_ROW px; on
 * the right edge of an indexed list the A-Z index (a press, then its drag, picks a letter). */
#define AS_TAP 8
static int as_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) }, type = I(event, EVENT_TYPE);
    if (!as.sheet) return 0;
    widget_to_local(as.sheet, xy);
    as_level *l = as_top();
    int indexed = as_indexed(l);
    if (type == EVT_POINTER_DOWN) {
        as.down_x = (short)xy[0], as.down_y = (short)xy[1], as.pressed = 1;
        as.drag = indexed && xy[0] >= 375 - AZ_EDGE_PX;
    }
    if (!as.pressed) return 0;
    if (as.drag) { /* the index: the finger's letter, the list at its first row */
        int letter = az_letter_at(I(event, EVENT_Y)), id = az_target(as.first, letter);
        if (id >= 0) l->sel = l->top = id;
        az_mark(as.sheet, letter, type != EVT_POINTER_UP_BEFORE, type != EVT_POINTER_UP_BEFORE);
        if (type == EVT_POINTER_UP_BEFORE) as.pressed = as.drag = 0;
        as_dirty();
        return type == EVT_POINTER_DOWN ? 0 : STOP;
    }
    if (type != EVT_POINTER_UP_BEFORE) return 0;
    as.pressed = 0;
    int dy = xy[1] - as.down_y, n = as_rows(l);
    if (dy > AS_TAP || dy < -AS_TAP) {
        l->top -= dy / AS_ROW;
        l->top = l->top < 0 ? 0 : l->top >= n ? (n ? n - 1 : 0) : l->top;
        if (l->sel < l->top) l->sel = l->top;
    } else {
        for (int i = l->top, y = 0; i < n && y <= as.down_y; ++i) {
            int h = as_row_h(l, i);
            if (as.down_y < y + h) {
                l->sel = i;
                st.touch_mode = 0;
                as_act();
                as_title();
                break;
            }
            y += h;
        }
    }
    as_dirty();
    return 0;
}

static int as_closed(void *ctx, void *event) {
    while (as.depth >= 0 && as.depth < AS_LEVELS) {
        as_drop(&as.lv[as.depth]);
        if (!as.depth--) break;
    }
    free(as.was);
    free(as.now);
    memset(&as, 0, sizeof as);
    return 0;
}

/* Add Songs for playlist dest (0 to 99, or ON_THE_GO) named name: the chooser, the marks' sets read
 * from the playlist as it is. */
void add_songs(int dest, const char *name) {
    int n;
    if (as.page || !(as.page = page_open("addsongs_page", as_closed, page_keyup))) return;
    as.dest = dest;
    tk_snprintf(as.name, sizeof as.name, "%s", name);
    as.was = calloc(AS_MAX_KEYS, sizeof *as.was);
    as.now = calloc(AS_MAX_KEYS, sizeof *as.now);
    void *songs = staged(playlist_query, (void *)(long)dest, &n);
    for (unsigned i = 0; songs && as.was && as.now && i < deque_size(songs) && i < AS_MAX_KEYS; ++i)
        as.was[as.nwas++] = as.now[as.nnow++] = as_key(deque_at(songs, i));
    if (songs) deque_destroy(songs);
    void *title = text(as.page, 0, bar_foot(as.page), 375, 28); /* what the status row reads */
    widget_set_name(title, PAGE_TITLE);
    widget_set_visible(title, 0, 0);
    as_title();
    as.sheet = widget_factory_create_widget(widget_factory(), "view", as.page, 0, bar_foot(as.page),
                                            375, page_room(as.page));
    widget_on(as.sheet, EVT_POINTER_DOWN, as_touch, 0);
    widget_on(as.page, EVT_POINTER_MOVE_BEFORE, as_touch, 0);
    widget_on(as.page, EVT_POINTER_UP_BEFORE, as_touch, 0);
    bar_title_sync(as.page);
}
