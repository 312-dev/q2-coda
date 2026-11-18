/* Now Playing's rating page and the album art's glow (docs/ipod.md#rating-page,
 * docs/oled-design.md#rating-page, docs/internals.md#rating-page).
 *
 * The page is the slide_view's page after the lyrics (tools/ipod.py np_rate_page: an empty view,
 * view_rate), reached by the same swipe. At rest on it the column goes full screen, as a tap makes
 * the lyrics (now_playing.c np_full), and back once the slide_view is on another page
 * (np_rate_poll); this paints it whole from its background hook: the glow, the song's
 * title and artist centred, five stars and the page dots. The stars are the song's rating
 * (ratings.c rate_now: 0 to 5, apart from the heart's like), filled in Text Primary up to it, the
 * rest outlined at NR_FAINT. The wheel moves a target star, drawn NR_STAR_BIG (an outline in full
 * Text Primary past the rating), and Centre keeps it; a tap on a star keeps it at once. The star
 * that is already the rating's top, tapped or kept by Centre, clears the rating: the page's "0".
 * Centre with no target puts it on the rating's top star (the first when unrated).
 *
 * The glow (the lyrics page's too) is the cover's colours washed across the window: the cover
 * averaged on an NR_GRID square grid, each cell's colour raised, dimmed and held under
 * NR_GLOW_MAX, spread bilinearly over the window and shaded by an ellipse round NR_GLOW_CY that
 * fades to black. It is a window-sized opaque bitmap made once for each cover Now Playing shows
 * (nr_glow_key: the art's key and the image's name), when a page that shows it first paints, and
 * drawn a row at a time after that (stock's image copy); never per frame. Leaving Now Playing
 * frees it. Light Mode has none: the page's paper. NR_GLOW_MAX keeps every channel at or under 60,
 * so Text Secondary lyrics over it keep WCAG's 4.5:1 (docs/oled-design.md#rating-page). */
#include "navigation.h"

/* The design's place on screen (the approved mock-up, 2026-11-18): the title's and artist's line
 * tops and px, the stars' top, size and gap, the target's size, the dots' top, size and gap. The
 * title stands on Now Playing's own title line, y 64, where the mock-up has 46. */
#define NR_TITLE_Y 64
#define NR_TITLE_PX 19
#define NR_ARTIST_GAP 3
#define NR_ARTIST_PX 15
#define NR_TEXT_X 24 /* the text column's inset each side */
#define NR_STARS_Y 128
#define NR_STAR 44     /* tools/art.py NP_STAR_PX */
#define NR_STAR_BIG 52 /* tools/art.py NP_STAR_BIG_PX: the target, 1.18 times */
#define NR_STAR_GAP 14
#define NR_STARS 5
#define NR_DOTS_Y 292
#define NR_DOT 6
#define NR_DOT_GAP 7
#define NR_FAINT 77       /* 30% of 255: the outlined stars and the other pages' dots */
#define NR_TARGET_MS 4000 /* the target goes back to rest this long after the last turn */
#define NR_POLL_MS 500 /* the rating read again while the page shows (a change from the server) */
/* The glow (the mock-up's radial wash): its centre's screen row and the ellipse's radii, in px;
 * where it reaches black, in 1/256 of them; the cover's grid; each cell's colour boost (1/256)
 * about its luma, its gain (1/256), and the channel cap. The window is the whole screen and the
 * status row an overlay on it, so a page full screen has the glow to the screen's top edge; a page
 * whose top is the status row's foot (the column) has Now Playing's black above it, so its glow
 * fades in over its first NR_TOP_FADE rows rather than meeting the black with an edge (nr_top_fade). */
#define NR_GLOW_CY 122
#define NR_GLOW_RX 273
#define NR_GLOW_RY 220
#define NR_GLOW_END 205
#define NR_GRID 4
#define NR_GLOW_SAT 358
#define NR_GLOW_GAIN 115
#define NR_GLOW_MAX 60
#define NR_TOP_FADE 32

/* The page's state, on the heap (the scratch arena is full): the page and the lyrics page that
 * show the glow, the glow and the cover it was made for, how many were made (a test's count), the
 * rating shown (-1: nothing that can be rated plays) and when it was read, the target (0 none) and
 * its timeout, where the last touch on the page came down, and whether the column is full screen
 * for the page. */
typedef struct {
    void *page, *lyric, *glow;
    unsigned glow_key, builds, read_at, timer;
    int glow_w, glow_h, rating, target, tap_x, tap_y, full;
} np_rate_t;
static np_rate_t *nr __attribute__((section(".scratch")));

unsigned np_rate_builds(void) { return nr ? nr->builds : 0; }

/* The page, while it is the slide_view's current page under the window in front */
static int nr_shown(void) {
    return nr && nr->page && st.np_slide && st.np_win &&
           window_manager_get_top_window(window_manager()) == st.np_win &&
           widget_get_prop_int(st.np_slide, "value", 0) == widget_index_of(nr->page);
}

static void nr_repaint(void) {
    if (nr && nr->page) widget_invalidate_force(nr->page, (void *)0);
}

/* ---- The glow ---- */

/* w's top left in its window, at rest: the slide_view's swipe moves its pages by the canvas, not
 * their boxes */
static void nr_in_window(void *w, int *x, int *y) {
    *x = *y = 0;
    for (void *p = w; p && p != st.np_win; p = P(p, W_PARENT)) *x += I(p, W_X), *y += I(p, W_Y);
}

/* The cover averaged on an NR_GRID grid into g (0xRRGGBB, row by row), over black where it is
 * clear; 0 when it can't be read. 8 x 8 samples a cell, so a large cover costs no more. */
static int nr_cover_grid(const char *image, unsigned *g) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    if (!image || !*image || widget_load_image(st.np_win, image, bitmap)) return 0;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data =
        format < 4 && w && h && w <= 4096 && h <= 4096 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (!data) return 0;
    const unsigned char *o = at[format];
    unsigned stride = bitmap_get_line_length(bitmap);
    for (unsigned cy = 0; cy < NR_GRID; ++cy)
        for (unsigned cx = 0; cx < NR_GRID; ++cx) {
            unsigned sum[3] = { 0, 0, 0 };
            for (unsigned j = 0; j < 8; ++j)
                for (unsigned i = 0; i < 8; ++i) {
                    unsigned sx = ((cx * 8 + i) * 2 + 1) * w / (NR_GRID * 16),
                             sy = ((cy * 8 + j) * 2 + 1) * h / (NR_GRID * 16);
                    const unsigned char *px = data + sy * stride + sx * 4;
                    unsigned a = px[o[3]];
                    for (int k = 0; k < 3; ++k) sum[k] += px[o[k]] * a / 255;
                }
            g[cy * NR_GRID + cx] = (sum[0] / 64) << 16 | (sum[1] / 64) << 8 | sum[2] / 64;
        }
    bitmap_unlock_buffer(bitmap);
    return 1;
}

static int nr_clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

/* A cell's colour for the glow: raised about its luma by NR_GLOW_SAT, dimmed by NR_GLOW_GAIN, then
 * scaled down until no channel passes NR_GLOW_MAX, in c[3] */
static void nr_cell(unsigned rgb, int *c) {
    int v[3] = { (int)(rgb >> 16 & 255), (int)(rgb >> 8 & 255), (int)(rgb & 255) },
        luma = (77 * v[0] + 150 * v[1] + 29 * v[2]) >> 8, top = 0;
    for (int k = 0; k < 3; ++k) {
        v[k] = nr_clamp(luma + ((v[k] - luma) * NR_GLOW_SAT >> 8), 0, 255) * NR_GLOW_GAIN >> 8;
        if (v[k] > top) top = v[k];
    }
    for (int k = 0; k < 3; ++k) c[k] = top > NR_GLOW_MAX ? v[k] * NR_GLOW_MAX / top : v[k];
}

/* The glow for the cover's grid g, into px (w x h, BGRA8888 rows of w), the window's top at screen
 * row wy */
static void nr_glow_paint(unsigned *px, int w, int h, int wy, const unsigned *g) {
    int cell[NR_GRID * NR_GRID][3];
    for (int i = 0; i < NR_GRID * NR_GRID; ++i) nr_cell(g[i], cell[i]);
    int cx = w / 2, cy = NR_GLOW_CY - wy;
    for (int y = 0; y < h; ++y) {
        /* the grid's rows about cell centres, in 1/256 of a cell */
        int fy = nr_clamp((2 * y + 1) * NR_GRID * 128 / h - 128, 0, (NR_GRID - 1) * 256),
            y0 = fy >> 8, y1 = y0 + (y0 < NR_GRID - 1), v = (y - cy) * 256 / NR_GLOW_RY;
        fy &= 255;
        unsigned *row = px + y * w;
        for (int x = 0; x < w; ++x) {
            int u = (x - cx) * 256 / NR_GLOW_RX,
                t2 = (u * u + v * v) * 256 / (NR_GLOW_END * NR_GLOW_END);
            if (t2 >= 256) {
                row[x] = 0xff000000u;
                continue;
            }
            int f = (256 - t2) * (256 - t2) >> 8,
                fx = nr_clamp((2 * x + 1) * NR_GRID * 128 / w - 128, 0, (NR_GRID - 1) * 256),
                x0 = fx >> 8, x1 = x0 + (x0 < NR_GRID - 1),
                grain = (int)(((unsigned)x * 73856093u ^ (unsigned)y * 19349663u) >> 8) % 3 - 1;
            fx &= 255;
            unsigned out = 0xff000000u;
            for (int k = 0; k < 3; ++k) {
                int a = cell[y0 * NR_GRID + x0][k] * (256 - fx) + cell[y0 * NR_GRID + x1][k] * fx,
                    b = cell[y1 * NR_GRID + x0][k] * (256 - fx) + cell[y1 * NR_GRID + x1][k] * fx,
                    c = ((a * (256 - fy) + b * fy) >> 16) * f >> 8;
                out |= (unsigned)nr_clamp(c ? c + grain : 0, 0, NR_GLOW_MAX) << (16 - 8 * k);
            }
            row[x] = out;
        }
    }
}

/* The cover the window shows now: the art's key (now_playing.c np_art_poll) and the image's name */
static unsigned nr_glow_key(const char **image) {
    *image = st.np_cover ? widget_get_prop_str(st.np_cover, "image", "") : "";
    return fnv(st.np_art ^ FNV_SEED, (const unsigned char *)*image) | 1;
}

/* The glow for the cover shown now, made when that cover is new; 0 under Light Mode, with no cover,
 * or without the memory */
static void *nr_glow(void) {
    if (!nr || !st.np_win || ipod_light()) return (void *)0;
    const char *image;
    unsigned key = nr_glow_key(&image);
    int w = I(st.np_win, W_W), h = I(st.np_win, W_H);
    if (nr->glow && key == nr->glow_key && w == nr->glow_w && h == nr->glow_h) return nr->glow;
    unsigned g[NR_GRID * NR_GRID];
    nr->glow_key = key;
    if (w <= 0 || h <= 0 || !nr_cover_grid(image, g))
        return nr->glow; /* the last cover's, if any */
    if (!nr->glow || w != nr->glow_w || h != nr->glow_h) {
        if (nr->glow) bitmap_destroy(nr->glow);
        nr->glow = bitmap_create_ex((unsigned)w, (unsigned)h, (unsigned)w * 4, LCD_BGRA8888);
        nr->glow_w = w, nr->glow_h = h;
        if (!nr->glow) return (void *)0;
        *(unsigned short *)((char *)nr->glow + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE: a row's copy */
    }
    unsigned *px = (unsigned *)bitmap_lock_buffer_for_write(nr->glow);
    if (!px) return (void *)0;
    nr_glow_paint(px, w, h, I(st.np_win, W_Y), g);
    bitmap_unlock_buffer(nr->glow);
    nr->builds++;
    return nr->glow;
}

/* Rows y to y + n of w's (a page or what is in one) from the glow, at alpha a: 1 when drawn, 0
 * when there is no glow to draw (lyrics.c lyrics_fade then fades to the page's ground). */
int np_glow_rows(void *w, void *canvas, int y, int n, unsigned a) {
    void *glow = nr && w && (nr->lyric || nr->page) ? nr_glow() : (void *)0;
    if (!glow || n <= 0) return 0;
    int x0, y0;
    nr_in_window(w, &x0, &y0);
    int sy = nr_clamp(y0 + y, 0, nr->glow_h), h = nr_clamp(y0 + y + n, 0, nr->glow_h) - sy;
    if (h <= 0) return 1;
    rect_t src = { nr_clamp(x0, 0, nr->glow_w), sy, I(w, W_W), h }, dst = { 0, sy - y0, src.w, h };
    if (src.x + src.w > nr->glow_w) src.w = dst.w = nr->glow_w - src.x;
    if (a < 255) canvas_set_global_alpha(canvas, a);
    canvas_draw_image(canvas, glow, &src, &dst);
    if (a < 255) canvas_set_global_alpha(canvas, 0xff);
    return 1;
}

/* ---- The page ---- */

static void nr_star(void *w, void *canvas, int i, int dy) {
    int big = i + 1 == nr->target, on = i < nr->rating, px = big ? NR_STAR_BIG : NR_STAR,
        cx = 375 / 2 + (i - NR_STARS / 2) * (NR_STAR + NR_STAR_GAP), cy = NR_STARS_Y + NR_STAR / 2;
    char name[24];
    tk_snprintf(name, sizeof name, "npstar_%s%s_%s", on ? "on" : "off", big ? "_big" : "",
                ipod_light() ? "l" : "d");
    if (!on && !big) canvas_set_global_alpha(canvas, NR_FAINT);
    paint_art(w, canvas, theme_art(name), cx - px / 2, cy - px / 2 - dy);
    if (!on && !big) canvas_set_global_alpha(canvas, 0xff);
}

/* The page dots: one for each page, the page's own in Text Primary, the rest at NR_FAINT */
static void nr_dots(void *canvas, int dy) {
    int n = st.np_slide ? (int)widget_count_children(st.np_slide) : 0,
        on = widget_index_of(nr->page), x = (375 - n * NR_DOT - (n - 1) * NR_DOT_GAP) / 2;
    unsigned ink = ui_ink(RGBA(RGB_PRIMARY)) & 0xffffffu;
    for (int i = 0; i < n; ++i, x += NR_DOT + NR_DOT_GAP) {
        rect_t r = { x, NR_DOTS_Y - dy, NR_DOT, NR_DOT };
        for (int row = 0; row < NR_DOT; ++row)
            round_row(canvas, &r, NR_DOT / 2, row, ink | (i == on ? 0xffu : NR_FAINT) << 24);
    }
}

/* The page's paint, in its canvas, on the glow: its layout is the screen's, the page's own top
 * dy on screen (the window's top and the column's), so it stands where the design has it at
 * rest full screen and slides in with the page */
static void nr_page_paint(void *w, void *canvas) {
    int dx, dy;
    nr_in_window(w, &dx, &dy);
    dy += I(st.np_win, W_Y);
    char text[256];
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int title = TYPE(NR_TITLE_PX), artist = TYPE(NR_ARTIST_PX);
    text[0] = 0;
    if (st.np_title) widget_get_text_utf8(st.np_title, text, sizeof text);
    ui_text(canvas, text, 1, title, RGB_PRIMARY, 0, 375 / 2, NR_TITLE_Y + title / 2 - dy,
            375 - 2 * NR_TEXT_X, 1);
    text[0] = 0;
    if (st.np_artist) widget_get_text_utf8(st.np_artist, text, sizeof text);
    ui_text(canvas, text, 0, artist, RGB_SECONDARY, 0, 375 / 2,
            NR_TITLE_Y + title + NR_ARTIST_GAP + artist / 2 - dy, 375 - 2 * NR_TEXT_X, 1);
    for (int i = 0; i < NR_STARS; ++i) nr_star(w, canvas, i, dy);
    nr_dots(canvas, dy);
    stock_canvas_set_fill_color(canvas, fill);
}

/* The glow's fade in over w's first NR_TOP_FADE rows, black over it from opaque to clear, when w's
 * top stands at or under the status row's foot (the column, not full screen). */
static void nr_top_fade(void *w, void *canvas) {
    int x0, y0;
    nr_in_window(w, &x0, &y0);
    if (y0 < bar_foot(st.np_win)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int y = 0; y < NR_TOP_FADE; ++y) {
        stock_canvas_set_fill_color(canvas, (unsigned)(255 - y * 255 / NR_TOP_FADE) << 24);
        canvas_fill_rect(canvas, 0, y, I(w, W_W), 1);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* ringnav_paint_bg, after stock's: the glow behind the lyrics page and the rating page, and the
 * rating page itself */
void np_rate_bg(void *w, void *canvas) {
    if (!nr || !w || (w != nr->page && w != nr->lyric) || !P(canvas, CANVAS_LCD)) return;
    if (np_glow_rows(w, canvas, 0, I(w, W_H), 0xff)) nr_top_fade(w, canvas);
    if (w == nr->page) nr_page_paint(w, canvas);
}

/* The rating read again (ratings.c rate_now) while the page shows, at most every NR_POLL_MS, or at
 * once with now; a change repaints it */
static void nr_read(int now) {
    unsigned at = (unsigned)time_now_ms();
    if (!now && at - nr->read_at < NR_POLL_MS) return;
    nr->read_at = at;
    int r = rate_now();
    if (r != nr->rating) nr->rating = r, nr_repaint();
}
/* Each lit pass (now_playing.c lyric_poll): the column full screen once the slide_view rests on the
 * page, untouched and no window sliding; back as it leaves it (a swipe drags the pages full height,
 * the column going back where they land); the rating read while the page shows. */
void np_rate_poll(void) {
    if (!nr) return;
    int on = np_rate_on();
    if (!st.np_full) nr->full = 0; /* ended for it: another Now Playing, the lyrics' tap */
    if (on && !st.np_full && st.np_lrc && !pointer_down() &&
        !window_manager_is_animating(window_manager()))
        np_full_set(1), nr->full = 1;
    else if (!on && nr->full)
        np_full_set(0), nr->full = 0;
    if (nr_shown()) nr_read(0);
}

static int nr_target_end(const void *unused) {
    nr->timer = 0;
    nr->target = 0;
    nr_repaint();
    return 0;
}

/* n stars kept for the playing track, and the page at rest */
static void nr_keep(int n) {
    stop_timer(&nr->timer);
    nr->target = 0;
    rate_now_set(n);
    nr_read(1);
    nr_repaint();
}

/* A tick of the wheel on the page: the target to the next star or the one before, from the
 * rating's top at rest, within the five. 1 when the page took it. */
int np_rate_key(unsigned key) {
    if ((key != KEY_NEXT && key != KEY_PREV) || !nr_shown() || st.scrub || st.np_stage) return 0;
    nr_read(1);
    if (nr->rating < 0) return 0; /* nothing to rate: the wheel stays the volume's */
    int from = nr->target ? nr->target : nr->rating;
    nr->target = nr_clamp(from + (key == KEY_NEXT ? 1 : -1), 1, NR_STARS);
    rearm(&nr->timer, nr_target_end, NR_TARGET_MS);
    nr_repaint();
    st.last_input = time_now_ms();
    return 1;
}

/* Centre on the page (now_playing.c np_centre, before its cycle): the target kept, the rating's own
 * top clearing it; with no target, the target on the rating's top. 1 when the page took it. */
int np_rate_centre(void) {
    if (!nr_shown() || st.scrub || st.np_stage) return 0;
    nr_read(1);
    if (nr->rating < 0) return 1;
    if (nr->target) {
        nr_keep(nr->target == nr->rating ? 0 : nr->target);
        return 1;
    }
    nr->target = nr->rating ? nr->rating : 1;
    rearm(&nr->timer, nr_target_end, NR_TARGET_MS);
    nr_repaint();
    return 1;
}

/* The star at the screen's (x, y), 1 to 5, or 0: its column a stride wide, the row NR_STAR_GAP
 * above and below the stars */
static int nr_star_at(int x, int y) {
    int stride = NR_STAR + NR_STAR_GAP, left = 375 / 2 - NR_STARS * stride / 2;
    if (y < NR_STARS_Y - NR_STAR_GAP || y >= NR_STARS_Y + NR_STAR + NR_STAR_GAP || x < left ||
        x >= left + NR_STARS * stride)
        return 0;
    return (x - left) / stride + 1;
}

/* A touch on the page: a lift within TAP_PX of its press on a star keeps that star (its rating's
 * own top clears it). Returns 0: a STOP would keep the slide_view's grab. */
static int nr_touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        nr->tap_x = x, nr->tap_y = y;
        return 0;
    }
    int tap = (unsigned)(x - nr->tap_x + TAP_PX) <= 2 * TAP_PX &&
              (unsigned)(y - nr->tap_y + TAP_PX) <= 2 * TAP_PX;
    nr->tap_x = 1 << 20; /* used or taken for a swipe: a later lift is no tap */
    int star = type == EVT_POINTER_UP_BEFORE && tap && nr_shown() ? nr_star_at(x, y) : 0;
    if (star) {
        nr_read(1);
        if (nr->rating >= 0) nr_keep(star == nr->rating ? 0 : star);
    }
    return 0;
}

/* playing_page_init: the page and the lyrics page found, the touch bound, the target at rest */
void np_rate_bind(void *win) {
    if (!nr && !(nr = calloc(1, sizeof *nr))) return;
    stop_timer(&nr->timer);
    nr->target = 0, nr->rating = -1, nr->read_at = 0, nr->tap_x = 1 << 20, nr->full = 0;
    nr->page = widget_lookup(win, "view_rate", 1);
    nr->lyric = (void *)0;
    for (void *p = st.np_lrc; p && st.np_slide; p = P(p, W_PARENT))
        if (P(p, W_PARENT) == st.np_slide) {
            nr->lyric = p;
            break;
        }
    static const unsigned touch[] = { EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned i = 0; nr->page && i < sizeof touch / sizeof *touch; ++i)
        widget_on(nr->page, touch[i], nr_touch, (void *)0);
    nr_read(1);
}

/* Now Playing gone (now_playing.c np_gone): its widgets forgotten and the glow freed */
void np_rate_gone(void) {
    if (!nr) return;
    stop_timer(&nr->timer);
    nr->page = nr->lyric = (void *)0;
    nr->target = nr->full = 0;
    if (nr->glow) bitmap_destroy(nr->glow);
    nr->glow = (void *)0;
    nr->glow_key = 0;
}

/* The slide_view on the rating page (now_playing.c lyric_poll rests it full screen) */
int np_rate_on(void) {
    return nr && nr->page && st.np_slide &&
           widget_get_prop_int(st.np_slide, "value", 0) == widget_index_of(nr->page);
}
