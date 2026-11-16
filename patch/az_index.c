/* The A-Z index (docs/oled-design.md#a-z-index, docs/internals.md#a-z-index): on a Library list in
 * name order with more than AZ_MIN_ROWS rows, under OLED, a column of letters down the right edge
 * that is not there at rest. A finger on the list's right edge (the last AZ_EDGE_PX) brings it up
 * and holds it while it drags: the finger's height picks one of the AZ_LETTERS "#A-Z", spread
 * evenly from AZ_TOP to AZ_BOTTOM however the column is drawn, and the list jumps so the first row
 * under that letter is at its top and selected (a letter with none: the next one that has rows,
 * else the last before it). A spin of the wheel at AZ_SPIN_DPS or faster brings it up too, the
 * selected row's letter lit and shown large beside it, as an iPod's fast scroll shows the letter
 * it passes. It goes AZ_SHOW_MS after the finger lifts or the last fast tick, fading over the last
 * AZ_FADE_MS, as the scroll indicator does (list_marks.c), which stays hidden meanwhile; so does
 * the fast-scroll letter (navigation.c paint_letter). While it shows, the rows' right column and
 * the selection bar's right end move in (az_right, az_bar_in) so nothing on a row runs under it.
 *
 * The lists: Songs (allmusic_page, titles A to Z: not its other orders, own_order, nor an
 * artist's or a genre's songs, which come unsorted) and the Library's Albums, Artists, Composers
 * and Genres (localclass_page and album_page by class), each a table over p_deque_showlist. A row's
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
#define AZ_PX 13 /* Inter Medium 11 px: AWTK sizes a face by its line, 1.21 em for Inter */
#define AZ_DOT 8 /* the lit letter's white disc's radius */
/* the bubble: AZ_BUB_W x AZ_BUB_H, AZ_BUB_R corners, ending AZ_BUB_GAP left of the column's centre,
 * its pointer AZ_PTR_W long and 2 * AZ_PTR_H tall towards it; its letter Inter Medium 30 px */
#define AZ_BUB_W 52
#define AZ_BUB_H 48
#define AZ_BUB_R 12
#define AZ_BUB_GAP 18
#define AZ_PTR_W 7
#define AZ_PTR_H 6
#define AZ_BUB_PX 36
#define AZ_BUB_RGB 0x2c2c2e
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
#define AZ_SHOW_MS 1000
#define AZ_FADE_MS 200
#define AZ_STEP_MS 40
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

/* Which list table w shows, AZ_*, or AZ_NONE: not an indexed one, or too short. */
static int az_kind(void *w) {
    if (!w || tk_strcmp(widget_get_type(w), "table_client")) return AZ_NONE;
    void *list = P(p_deque_showlist, 0), *win = window_of(w);
    int rows = I(w, TABLE_ROWS);
    if (rows <= AZ_MIN_ROWS || !list || (int)deque_size(list) != rows || !win) return AZ_NONE;
    const char *page = widget_get_prop_str(win, "name", "");
    int cls = I(g_class_type, 0);
    if (!tk_strcmp(page, "allmusic_page"))
        return own_order(page) || cls == 0xff07 || cls == 0xff09 ? AZ_NONE : AZ_SONGS;
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

/* The strip's alpha now: 255 while held or until the fade, then falling to 0 at AZ_SHOW_MS. */
static unsigned az_alpha(void) {
    if (!az.list) return 0;
    unsigned gone = (unsigned)time_now_ms() - az.at;
    if (az.held || gone <= AZ_SHOW_MS - AZ_FADE_MS) return 255;
    return gone >= AZ_SHOW_MS ? 0 : 255u * (AZ_SHOW_MS - gone) / AZ_FADE_MS;
}

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
    int gone = !az.held && (unsigned)time_now_ms() - az.at >= AZ_SHOW_MS;
    if (top && top == az.page) {
        rect_t r = { 375 - AZ_X - AZ_BUB_GAP - AZ_BUB_W, 0, AZ_X + AZ_BUB_GAP + AZ_BUB_W,
                     I(top, W_H) };
        widget_invalidate_force(top, gone ? (void *)0 : &r);
    }
    if (gone)
        az.list = az.page = (void *)0, az.letter = -1, az.bubble = 0;
    else if (!az.held)
        az.timer = timer_add(az_due, (void *)0, AZ_STEP_MS);
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
        rearm(&az.timer, az_due, AZ_SHOW_MS - AZ_FADE_MS);
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
    int light = ipod_light(), x = 375 - AZ_X - I(canvas, CANVAS_X);
    unsigned ink = light ? 0x000000 : 0xffffff, paper = light ? 0xffffff : 0x000000;
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
