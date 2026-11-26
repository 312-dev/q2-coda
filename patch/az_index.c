/* The A-Z index (docs/oled-design.md#a-z-index, docs/internals.md#a-z-index): on a Library list in
 * name order with more than AZ_MIN_ROWS rows, under OLED, a column of letters down the right edge
 * that is not there at rest. A finger on the list's right edge (the last AZ_EDGE_PX) brings it up
 * and holds it while it drags: the finger's height picks one of the AZ_LETTERS "#A-Z", spread
 * evenly from AZ_TOP to AZ_BOTTOM however the column is drawn, and the list jumps so the first row
 * under that letter is at its top and selected (a letter with none: the next one that has rows,
 * else the last before it). A spin of the wheel at AZ_SPIN_DPS or faster brings it up too, the
 * selected row's letter lit and shown large beside it, as an iPod's fast scroll shows the letter
 * it passes. It goes UI_SHOW_MS after the finger lifts or the last fast tick, fading over the last
 * UI_FADE_MS, as the scroll indicator does (list_marks.c), which stays hidden meanwhile; so does
 * the fast-scroll letter (navigation.c paint_letter). While it shows, the rows' right column and
 * the selection bar's right end move in (az_right, az_bar_in) so nothing on a row runs under it.
 *
 * The lists: Songs (allmusic_page, titles A to Z: not its other orders, nor an artist's or a
 * genre's songs, which come unsorted; name_sorted) and the Library's Albums, Artists, Composers and
 * Genres (localclass_page and album_page by class), each a table over p_deque_showlist. A row's
 * letter is its name's first character past the article the sort skips (ringnav_sort_key), A to Z
 * in either case, anything else "#", as stock's own Initial is (catalogue.c initial). While the
 * strip is hidden this costs a test of wheel_speed on each list tick, of az.list on each paint and
 * row, and az_kind's few compares on each press. */
#include "navigation.h"

#define AZ_LETTERS 27 /* "#", then A to Z */
#define AZ_X 13       /* the column's centre, from the right edge */
#define AZ_TOP 80 /* the straight part of the glass, screen rows (docs/oled-design.md#corners) */
#define AZ_BOTTOM 240
#define AZ_SLOT 11 /* a letter's height: past AZ_SLOTS of them the column folds */
#define AZ_SLOTS ((AZ_BOTTOM - AZ_TOP) / AZ_SLOT)
#define AZ_PX TYPE(13) /* Inter Medium 13 px */
#define AZ_DOT 8 /* the lit letter's white disc's radius */
/* the bubble: AZ_BUB_W x AZ_BUB_H, AZ_BUB_R corners, ending AZ_BUB_GAP left of the column's centre,
 * its pointer AZ_PTR_W long and 2 * AZ_PTR_H tall towards it; its letter Inter Medium 22 px */
#define AZ_BUB_W 52
#define AZ_BUB_H 48
#define AZ_BUB_R 12
#define AZ_BUB_GAP 18
#define AZ_PTR_W 7
#define AZ_PTR_H 6
#define AZ_BUB_PX TYPE(22)
#define AZ_BUB_RGB RGB_RAISED_2
/* while it shows: the right column AZ_RIGHT_IN px in (W - 32 from W - 28), the bar's end AZ_BAR_IN
 */
#define AZ_RIGHT_IN 4
#define AZ_BAR_IN 8
/* AZ_SPIN_DPS (navigation.h): a tick at that speed or faster, degrees a second as the encoder
 * thread measures it (wheel_speed, averaged as Rockbox's iPod driver does), is a fast spin: the
 * speed at which Rockbox's list acceleration, (speed / 270)^4, first moves 2 rows a click, which is
 * where the volume's goes from 1 step a tick to 2 (volume.c vol_accel,
 * docs/internals.md#wheel-volume). At 50% Wheel Sensitivity that is 0.83 turns a second, about 6
 * ticks. AZ_MIN_ROWS and AZ_EDGE_PX are there too, shared with add_songs.c. */
#define AZ_BOUND "_az_bound"

enum { AZ_NONE, AZ_SONGS, AZ_ALBUMS, AZ_ARTISTS, AZ_COMPOSERS, AZ_GENRES };

static struct {
    void *list;  /* the table the strip shows on, 0 while hidden; compared, never followed */
    void *page;  /* ... its window */
    unsigned at; /* the finger's lift or the last fast tick, time_now_ms */
    unsigned timer;
    int first[AZ_LETTERS]; /* a touch's list: each letter's first row, -1 none */
    signed char letter;    /* the lit letter, -1 none */
    unsigned char held;    /* a finger is on the strip */
    unsigned char bubble;  /* the big letter shows: dragging, or a fast spin */
} az __attribute__((section(".scratch")));

/* The letter s files under: 0 for "#", 1 to 26 for A to Z, past a leading article. */
int az_initial(const char *s) {
    char key[64];
    tk_snprintf(key, sizeof key, "%s", s ? s : "");
    ringnav_sort_key(key);
    unsigned c = (unsigned char)key[0];
    if (c >= 'a' && c <= 'z') c -= 32;
    return c >= 'A' && c <= 'Z' ? (int)(c - 'A' + 1) : 0;
}

/* The top page's list is a name-sorted Library list: the one rule for the fast-scroll letter
 * (navigation.c, where the wheel arms it) and the A-Z index (az_kind). An explicit list, as every
 * other list is in an order of its own: Wi-Fi's networks by signal, Bluetooth's devices, Settings,
 * the queue, Most Played, Era's albums by year, a card app's or a stream's rows as their source
 * gives them (docs/internals.md#a-z-index).
 * allmusic_page shows lists of both kinds, so there the class and the Sort decide: Recent,
 * Frequent and Recently Added (0xf007 to 0xf009) and Songs under Time added (g_sort_flag 3 or 4,
 * or song_sort.c's own orders) are newest or most played first; a playlist's songs (0xff13) not by
 * Title (playlist_songs.c, its cd_num at classinfo +4) are as added, or the newest first; an
 * artist's and a genre's songs (0xff07, 0xff09) come as the database holds them. Edit Playlist's
 * songs are as added too (ple_listing); its Add Songs picker is the whole library A to Z. */
int name_sorted(const char *page) {
    static const char *const sorted[] = { "localclass_page", "album_page", "artistinfo_page",
                                          "folder_page", "coverflow_page" };
    if (!tk_strcmp(page, "allmusic_page")) {
        int cls = I(g_class_type, 0), sort = I(g_sort_flag, 0);
        return !(
            (cls >= 0xf007 && cls <= 0xf009) || cls == 0xff07 || cls == 0xff09 ||
            (cls == 0xff13 && playlist_songs_order(I(g_local_classinfo_save, 4)) != PSO_TITLE) ||
            (cls == 0xf001 && (sort == 3 || sort == 4 || song_sort_own())));
    }
    if (!tk_strcmp(page, "playlistedit_page")) return !ple_listing();
    for (unsigned i = 0; i < sizeof sorted / sizeof *sorted; ++i)
        if (!tk_strcmp(page, sorted[i])) return 1;
    return 0;
}

/* Which list table w shows, AZ_*, or AZ_NONE: not an indexed one, or too short. */
static int az_kind(void *w) {
    if (!w || tk_strcmp(widget_get_type(w), "table_client")) return AZ_NONE;
    void *list = P(p_deque_showlist, 0), *win = window_of(w);
    int rows = I(w, TABLE_ROWS);
    if (rows <= AZ_MIN_ROWS || !list || (int)deque_size(list) != rows || !win) return AZ_NONE;
    const char *page = widget_get_prop_str(win, "name", "");
    int cls = I(g_class_type, 0);
    if (!tk_strcmp(page, "allmusic_page")) return name_sorted(page) ? AZ_SONGS : AZ_NONE;
    if (tk_strcmp(page, "localclass_page") && tk_strcmp(page, "album_page")) return AZ_NONE;
    return cls >= CLASS_ALBUMS && cls <= 0xf006 ? AZ_ALBUMS + (cls - CLASS_ALBUMS) : AZ_NONE;
}

/* Row r's letter in a list of kind k, or -1 for a class list's Unknown row, which sorts last. */
static int az_letter_of(void *r, int k) {
    static const unsigned char field[] = { REC_ALBUM, REC_ARTIST, 0x20, 0x1c }; /* class_query's */
    char name[256];
    if (k == AZ_SONGS) return az_initial(track_name(name, sizeof name, r));
    return I(r, REC_ID) < 0 ? -1 : az_initial(P(r, field[k - AZ_ALBUMS]));
}

/* navigation's wheel in its letter steps (wheel_accel.c): the row a whole-letter tick from row id of
 * table w goes to, dir 1 or -1. Down: the next letter's first row (the list's last row from the
 * last letter); up: the current letter's first row, or the one before's when id is that already.
 * Never past the list's ends. -1 when w is no indexed list. The bubble shows the letter, as a
 * fast spin's does. */
int az_letter_step(void *w, int id, int dir) {
    int k = az_kind(w);
    void *list = P(p_deque_showlist, 0);
    int n = list ? (int)deque_size(list) : 0;
    if (!k || id < 0 || id >= n) return -1;
    int at = az_letter_of(deque_at(list, (unsigned)id), k), i = id;
    if (dir > 0) {
        while (i + 1 < n && az_letter_of(deque_at(list, (unsigned)(i + 1)), k) == at) ++i;
        i = i + 1 < n ? i + 1 : n - 1;
    } else {
        if (i > 0 && az_letter_of(deque_at(list, (unsigned)(i - 1)), k) != at) --i; /* at its start */
        at = az_letter_of(deque_at(list, (unsigned)i), k);
        while (i > 0 && az_letter_of(deque_at(list, (unsigned)(i - 1)), k) == at) --i;
    }
    int letter = az_letter_of(deque_at(list, (unsigned)i), k);
    if (letter >= 0) az_mark(w, letter, 0, 1);
    return i;
}

/* The row a letter jumps to: its first, else the next letter's that has rows, else the last
 * before it; -1 when the list has none. */
int az_target(const int *first, int letter) {
    for (int k = letter; k < AZ_LETTERS; ++k)
        if (first[k] >= 0) return first[k];
    for (int k = letter - 1; k >= 0; --k)
        if (first[k] >= 0) return first[k];
    return -1;
}

/* The letter at screen row y: the finger maps evenly over all of them, folded or not. */
int az_letter_at(int y) {
    int k = (y - AZ_TOP) * AZ_LETTERS / (AZ_BOTTOM - AZ_TOP);
    return y < AZ_TOP ? 0 : k >= AZ_LETTERS ? AZ_LETTERS - 1 : k;
}

/* Letter k's centre row on screen, in 1/16 px. */
static int az_letter_y16(int k) {
    return AZ_TOP * 16 + (AZ_BOTTOM - AZ_TOP) * 16 * (2 * k + 1) / (2 * AZ_LETTERS);
}

/* The column as drawn: every letter while they fit AZ_SLOTS, else iOS's fold, every so many kept
 * evenly from "#" to Z with a dot between each two: its n entries in out, each a letter 0-26 or -1
 * for a dot. */
int az_column(signed char *out) {
    if (AZ_LETTERS <= AZ_SLOTS) {
        for (int k = 0; k < AZ_LETTERS; ++k) out[k] = (signed char)k;
        return AZ_LETTERS;
    }
    int keep = (AZ_SLOTS + 1) / 2, n = 0;
    for (int i = 0; i < keep; ++i) {
        out[n++] = (signed char)(((AZ_LETTERS - 1) * 2 * i + (keep - 1)) / (2 * (keep - 1)));
        if (i < keep - 1) out[n++] = -1;
    }
    return n;
}

/* The strip's alpha now: 255 while held, else ui_kit.h's show-then-fade from its last arm. */
static unsigned az_alpha(void) { return !az.list ? 0 : az.held ? 255 : ui_fade_alpha(az.at); }

/* Whether the strip is up on surface w (or on the list view w holds it in). */
int az_on(void *w) { return az.list && w && (w == az.list || w == P(az.list, W_PARENT)); }
/* The right column's distance from the right edge on w: the set's, or AZ_RIGHT_IN more while the
 * strip is up on it. */
int az_right(void *w) { return geom_val(V_RIGHT_X) + (az_on(w) ? AZ_RIGHT_IN : 0); }
/* ... and how much the selection bar's right end comes in. */
int az_bar_in(void *w) { return az_on(w) ? AZ_BAR_IN : 0; }

/* The top window repainted: whole when the strip has gone (the rows move back), else the strip's
 * columns as it fades. */
static int az_due(const void *info) {
    (void)info;
    az.timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    int gone = !az.held && (unsigned)time_now_ms() - az.at >= UI_SHOW_MS;
    if (top && top == az.page) {
        rect_t r = { 375 - AZ_X - AZ_BUB_GAP - AZ_BUB_W, 0, AZ_X + AZ_BUB_GAP + AZ_BUB_W,
                     I(top, W_H) };
        widget_invalidate_force(top, gone ? (void *)0 : &r);
    }
    if (gone)
        az.list = az.page = (void *)0, az.letter = -1, az.bubble = 0;
    else if (!az.held)
        az.timer = timer_add(az_due, (void *)0, UI_FADE_STEP_MS);
    return 0;
}

/* The strip up on w, its fade starting over from now. */
static void az_show(void *w) {
    if (az.list != w) widget_invalidate_force(w, (void *)0); /* the rows move in */
    az.list = w;
    az.page = window_of(w);
    az.at = (unsigned)time_now_ms();
    if (az.held)
        stop_timer(&az.timer);
    else
        rearm(&az.timer, az_due, UI_SHOW_MS - UI_FADE_MS);
}

/* navigation.c's wheel: a tick moved table w's selection to row id. A fast one brings the strip up
 * with the row's letter large beside it; a slow one keeps the letter in step while it fades. */
void az_wheel(void *w, int id) {
    if (az.held || (wheel_speed < AZ_SPIN_DPS && !az_on(w))) return;
    int k = az_kind(w);
    void *list = P(p_deque_showlist, 0);
    if (!k || id < 0 || (unsigned)id >= deque_size(list)) return;
    int letter = az_letter_of(deque_at(list, (unsigned)id), k), was = az.letter, had = az.bubble;
    az.letter = (signed char)(letter < 0 ? az.letter : letter);
    az.bubble = wheel_speed >= AZ_SPIN_DPS;
    if (az.bubble) az_show(w);
    /* the window: the lit "#" reaches above Songs' table, under its Shuffle row */
    if (az.letter != was || az.bubble != had) widget_invalidate_force(window_of(w), (void *)0);
}

/* A list the payload paints itself (add_songs.c): the strip up on its surface w with letter lit,
 * held while a finger is on it, the bubble beside it while bubble; let go, it fades as after a
 * lift. */
void az_mark(void *w, int letter, int held, int bubble) {
    az.letter = (signed char)letter;
    az.held = (unsigned char)held;
    az.bubble = (unsigned char)bubble;
    az_show(w);
    widget_invalidate_force(window_of(w), (void *)0);
}

/* A finger at screen row y on the strip: its letter lit, and the list at that letter's rows. */
static void az_drag(void *w, int y) {
    int k = az_letter_at(y);
    if (k == az.letter) return;
    az.letter = (signed char)k;
    int id = az_target(az.first, k);
    if (id >= 0) ringnav_jump(w, id);
    widget_invalidate_force(window_of(w), (void *)0); /* the bubble reaches past the list's rows */
}

/* The page's move and release while a finger holds the strip (bound once a page, as pull_event
 * is): swallowed, so the list neither scrolls nor takes a tap. */
static int az_event(void *page, void *event) {
    unsigned type = I(event, EVENT_TYPE);
    if (type == EVT_DESTROY || type == EVT_WINDOW_CLOSE) {
        if (az.page == page) {
            stop_timer(&az.timer);
            az.list = az.page = (void *)0, az.held = 0;
        }
        return 0;
    }
    if (!az.held || page != az.page) return 0;
    if (type == EVT_POINTER_MOVE_BEFORE) {
        az_drag(az.list, I(event, EVENT_Y));
        return STOP;
    }
    az.held = az.bubble = 0; /* EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT */
    az_show(az.list);
    widget_invalidate_force(page, (void *)0);
    return STOP;
}

/* Whether a finger holds the strip as event comes: navigation.c's touch hook leaves its moves to
 * az_event. A press while it holds is a new touch, the last one's lift missed: the hold ends there
 * and the strip fades as after a lift. */
int az_held(void *event) {
    if (az.held && event && I(event, EVENT_TYPE) == EVT_POINTER_DOWN) {
        az.held = az.bubble = 0;
        az_show(az.list);
    }
    return az.held;
}

/* navigation.c's touch hook, on a press after stock's: one on the right edge of an indexed list
 * takes the touch (the rows it pressed are aborted) and jumps. Returns 1 if it took it. */
int az_touch(void *w, void *event) {
    int k = az_kind(w);
    if (!k || !event) return 0;
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(w, xy);
    xy[1] -= I(w, TABLE_TOP); /* to_local counts the list's scroll: the press's place in the view */
    if (xy[0] < I(w, W_W) - AZ_EDGE_PX || xy[0] >= I(w, W_W) || xy[1] < 0 || xy[1] >= I(w, W_H))
        return 0;
    void *page = window_of(w), *list = P(p_deque_showlist, 0);
    if (!widget_get_prop_int(page, AZ_BOUND, 0)) {
        static const unsigned types[] = { EVT_DESTROY, EVT_WINDOW_CLOSE, EVT_POINTER_ABORT,
                                          EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE };
        for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
            if (!widget_on(page, types[i], az_event, page)) {
                while (i) widget_off_by_func(page, types[--i], az_event, page);
                return 0;
            }
        prop(page, AZ_BOUND, 1);
    }
    for (int i = 0; i < AZ_LETTERS; ++i) az.first[i] = -1;
    for (unsigned i = 0, n = deque_size(list); i < n; ++i) {
        int letter = az_letter_of(deque_at(list, i), k);
        if (letter >= 0 && az.first[letter] < 0) az.first[letter] = (int)i;
    }
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, page, I(event, EVENT_X), I(event, EVENT_Y));
    az.held = 0; /* the abort is the rows', not a lift */
    widget_dispatch_event_to_target_recursive(page, abort_event);
    az.held = az.bubble = 1;
    az.letter = -1;
    az_show(w);
    az_drag(w, I(event, EVENT_Y));
    return 1;
}

/* text c, Inter Medium at px, centred on x, y (the surface's), in 0xAARRGGBB */
static void az_text(void *canvas, unsigned c, int x, int y, int box, unsigned px, unsigned argb) {
    unsigned s[2] = { c, 0 };
    rect_t r = { x - box / 2, y - box / 2, box, box };
    final_text(canvas, "inter-medium", s, &r, px, argb);
}

/* ringnav_paint: the strip over its list's window w, after everything in it (the list's own surface
 * may start below AZ_TOP, under Songs' Shuffle row). White letters on the black (Light Mode's
 * flip: black on its white), the lit one in a disc of the ink with the paper's letter. */
void az_paint(void *w, void *canvas) {
    if (!az.list || w != az.page || !P(canvas, CANVAS_LCD)) return;
    unsigned a = az_alpha();
    if (!a) return;
    int oy = I(canvas, CANVAS_Y); /* the canvas's origin on screen */
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int light = ipod_light(), x = 375 - AZ_X - page_ox(canvas, w);
    unsigned ink = tok(TOK_PRIMARY), paper = light ? 0xffffff : 0x000000;
    signed char col[AZ_LETTERS];
    int n = az_column(col);
    for (int i = 0; i < n; ++i) {
        int y = AZ_TOP + ((AZ_BOTTOM - AZ_TOP) * (2 * i + 1) + n) / (2 * n) - oy;
        az_text(canvas,
                col[i] < 0 ? 0x2022
                : col[i]   ? 'A' + (unsigned)col[i] - 1
                           : '#',
                x, y, AZ_SLOT + 4, AZ_PX, a << 24 | ink);
    }
    if (az.letter >= 0) {
        int y = (az_letter_y16(az.letter) + 8) / 16 - oy;
        unsigned c = az.letter ? 'A' + (unsigned)az.letter - 1 : '#';
        rect_t disc = { x - AZ_DOT, y - AZ_DOT, 2 * AZ_DOT, 2 * AZ_DOT };
        fill_box(canvas, &disc, (RGBA(ink) & 0xffffffu) | a << 24, AZ_DOT);
        az_text(canvas, c, x, y, 2 * AZ_DOT, AZ_PX, a << 24 | paper);
        if (az.bubble) {
            int right = x - AZ_BUB_GAP;
            unsigned bub = (RGBA(AZ_BUB_RGB) & 0xffffffu) | a << 24;
            rect_t b = { right - AZ_BUB_W, y - AZ_BUB_H / 2, AZ_BUB_W, AZ_BUB_H };
            fill_box(canvas, &b, bub, AZ_BUB_R);
            stock_canvas_set_fill_color(canvas, bub);
            for (int i = 0; i < AZ_PTR_W; ++i) { /* the pointer, a column at a time */
                int h = AZ_PTR_H * (AZ_PTR_W - i) / AZ_PTR_W;
                canvas_fill_rect(canvas, right + i, y - h, 1, 2 * h);
            }
            az_text(canvas, c, right - AZ_BUB_W / 2, y, AZ_BUB_H, AZ_BUB_PX, a << 24 | 0xffffff);
        }
    }
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}
