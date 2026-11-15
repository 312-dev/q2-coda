/* Now Playing's About (docs/ipod.md#now-playing): ⋯'s About row opens the track's details as a
 * page of their own. They are Now Playing's info page (tools/ipod.py np_info_out), which the build
 * takes out of the swipe and leaves hidden in Now Playing's window, where stock's np_info finds
 * and fills its labels. The page moves that view into itself while it is open, at the place it
 * has in Now Playing's window (the page's window has the same box; below the title where that is
 * a row of the page), and gives it back hidden as
 * it closes, so each theme's layout of the details stays its own. Return goes back to what
 * opened it, as ⋯'s other rows' pages do: under OLED ⋯'s panel, else ⋯'s page. While it is open
 * Now Playing's fills go to it (navigation.c ringnav_np_info).
 *
 * Under OLED the details stay hidden and are the source of an info grid the page paints
 * (docs/oled-design.md#track-details): a header of the cover, title, artist and length, a quality
 * line (a Lossless, Hi-Res Lossless or DSD badge, then the format's spec), and a grid of small grey
 * names over white values, two a row, in AB_CELLS' order, a cell whose value is unknown left out
 * and the rest closing up, then Location across the row. The header's top stands above the window,
 * under the status row: np_about_cap draws it again in the status bar's background, as an artist's
 * monogram is (artist_page.c artist_cap). A read-only page has no selection: the wheel scrolls it,
 * a row at a time, when it is taller than the screen (np_about_key). */
#include "navigation.h"

#define AB_HEAD_Y 46 /* screen rows: the cover's top */
#define AB_ART 56
#define AB_ART_R 6
#define AB_ART_X 24
#define AB_TEXT_X 92
#define AB_TITLE_DY 10 /* each header line's centre below AB_HEAD_Y */
#define AB_ARTIST_DY 31
#define AB_LEN_DY 49
#define AB_QUALITY_Y 112 /* the quality line's badge, AB_BADGE_H high */
#define AB_BADGE_H 20
#define AB_BADGE_R 6
#define AB_GLYPH 12 /* tools/art.py QUALITY_PX, centred AB_GLYPH_DX in */
#define AB_GLYPH_DX 11
#define AB_BADGE_TEXT 21 /* the badge's label from its left edge */
#define AB_BADGE_PAD 30  /* the badge's width past its label's */
#define AB_SPEC_GAP 10
#define AB_GRID_Y 138
#define AB_ROW 44
#define AB_COL2 192
#define AB_NAME_DY 8 /* a cell's name's centre below its top, and its value's */
#define AB_VALUE_DY 26
#define AB_VALUE_W 150
#define AB_SECOND 0xababab /* Text Secondary */
#define AB_THIRD 0x858585  /* Text Tertiary */
#define AB_MONO 0x2c2c2e   /* no cover: an artist page's placeholder grey */
/* AWTK's sizes for the design's px (Inter's line is 1.21 em: assets/themes/oled/theme.xml) */
#define PX_20 24
#define PX_17 21
#define PX_15 18
#define PX_13 16
#define DOT " \xc2\xb7 " /* " · " */

enum { CELL_ALBUM, CELL_YEAR, CELL_GENRE, CELL_KBPS, CELL_SIZE, CELL_LOCATION, AB_CELLS };
enum { BADGE_NONE, BADGE_LOSSLESS, BADGE_HIRES, BADGE_DSD };

typedef struct {
    char name[32], value[160];
} ab_cell;

static struct {
    void *page, *info, *home; /* the open page, the details it holds and their window */
    void *body;               /* OLED's grid: the page's painted view */
    int off, end, n, badge;   /* the scroll, the content's last row on screen, cells shown */
    int wide;                 /* the last cell is Location, across the row */
    unsigned key, cap;        /* what was painted (hashed); the bar asked to repaint */
    struct {                  /* hashed whole: zeroed before each fill */
        char title[160], artist[160], len[12], spec[64], art[128];
        unsigned art_key;
        ab_cell cell[AB_CELLS];
    } t;
} ab __attribute__((section(".scratch")));

void *np_about_page(void) { return ab.page; }

/* The details back in Now Playing's window, hidden and its first child, if both are still there;
 * with Now Playing gone first they stay the page's and go with it. */
static void ab_return(void) {
    void *info = ab.info, *home = ab.home, *page = ab.page;
    ab.info = ab.home = (void *)0;
    if (!info || !home || !page || P(info, W_PARENT) != page) return;
    widget_remove_child(page, info);
    widget_set_visible(info, 0, 0);
    widget_add_child(home, info);
    /* first again, as the build put it: stock's AWTK hands a touch to a hidden widget too, and
     * last it took every touch on Now Playing (tools/ipod.py np_info_out) */
    widget_restack(info, 0);
}

static int ab_closed(void *page, void *event) {
    if (ab.page != page) return 0;
    ab_return();
    ab.page = ab.body = (void *)0;
    ab.off = ab.end = ab.n = ab.badge = ab.wide = 0;
    ab.key = 0;
    return 0;
}

static int ab_key(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    ab_return(); /* before the close, while every widget is whole */
    return page_keyup(ctx, event);
}

/* Now Playing's window going (navigation.c np_gone) while the page holds its details: they stay
 * the page's, and go with it. */
void np_about_np_gone(void *win) {
    if (ab.home == win) ab.home = (void *)0;
}

/* ⋯'s About row (onlinematch.c more_rows). */
int np_about_open(void *ctx, void *event) {
    void *home = st.np_win, *info = home ? widget_lookup(home, "view_info", 1) : (void *)0;
    if (ab.page || !info || !(ab.page = page_open("npabout_page", ab_closed, ab_key))) return 0;
    void *title = page_title(ab.page, "About", 1);
    widget_remove_child(P(info, W_PARENT), info);
    widget_add_child(ab.page, info);
    if (oled_pages()) { /* the grid, over the window; the details stay hidden, its source */
        ab.body = widget_factory_create_widget(widget_factory(), "view", ab.page, 0, 0, 375,
                                               320 - geom_val(V_BAR));
    } else {
        /* under the title where the theme shows it as the page's first row: Centred Now
         * Playing's details start at the window's top, where its Return and More stood */
        int below = title && widget_get_visible(title) ? I(title, W_Y) + I(title, W_H) : 0;
        if (I(info, W_Y) < below)
            widget_move_resize(info, I(info, W_X), below, I(info, W_W), I(info, W_H));
        widget_set_visible(info, 1, 0);
    }
    ab.info = info, ab.home = home;
    ringnav_np_info(ab.page); /* filled at once, not at Now Playing's next tick */
    return 0;
}

/* ---- OLED's info grid ---- */

/* The bar painted again once the page has come to rest (artist_page.c cap_due's way): asked from
 * a change, where an invalidate of the bar could be lost with the frame, so from the next pass on,
 * until the window manager has no slide running. */
static int cap_due(const void *info) {
    (void)info;
    void *bar = *(void *const *)system_bar;
    ab.cap = 0;
    if (!ab.body) return 0;
    if (window_manager_is_animating(window_manager())) {
        ab.cap = 1;
        return 8; /* RET_REPEAT */
    }
    if (bar) widget_invalidate_force(bar, (void *)0);
    return 0;
}
static void cap_ask(void) {
    if (!ab.cap) ab.cap = timer_add(cap_due, (void *)0, 0) | 1;
}

/* The scroll's furthest: the content's last row's bottom on the set's list end (list_fit_end, or
 * the screen's bottom), 0 when it fits. */
static int ab_room(void) {
    int fit = list_fit_end(), over = ab.end - (fit ? fit : 320);
    return over > 0 ? over : 0;
}

/* A key on the page (navigation.c ringnav, as Spotify's page takes its own): a tick of the wheel
 * scrolls a row, as far as the last row, and the page takes every tick (stock's volume never
 * sees one). 1 when the page took it. */
int np_about_key(void *top, unsigned key) {
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (!ab.body || top != ab.page || !dir) return 0;
    int off = ab.off + dir * AB_ROW, room = ab_room();
    off = off < 0 ? 0 : off > room ? room : off;
    if (off != ab.off) {
        ab.off = off;
        widget_invalidate_force(ab.body, (void *)0);
        cap_ask(); /* the header's top in the bar shows only at the page's top */
    }
    return 1;
}

/* label's text into out (n bytes): 1 when it says something known, not empty and not the
 * language's unknown (unk) or ringnav_np_info's "Unknown". */
static int ab_get(void *win, const char *name, char *out, unsigned n, const char *unk) {
    void *w = widget_lookup(win, name, 1);
    if (!w || widget_get_text_utf8(w, out, n)) *out = 0;
    return *out && strcmp(out, "Unknown") && strcmp(out, unk);
}
/* s's leading number in tenths ("44.1 kHz" 441, "16-bit" 160), its digits after the point
 * rounded; -1 when s starts with no digit. *rest: past it. */
static int ab_tenths(const char *s, const char **rest) {
    int v = 0, frac = 0, k = 0;
    if (*s < '0' || *s > '9') return -1;
    for (; *s >= '0' && *s <= '9'; ++s) v = v * 10 + *s - '0';
    if (*s == '.')
        for (++s; *s >= '0' && *s <= '9'; ++s, ++k)
            if (k < 2) frac = frac * 10 + *s - '0';
    /* frac: the first two digits after the point, hundredths (one digit: tenths) */
    int tenths = k >= 2 ? (frac + 5) / 10 : frac;
    if (rest) *rest = s;
    return v * 10 + tenths;
}
/* "1978 kbps" as "1,978 kbps": a thousands separator in the leading whole number. */
static void ab_thousands(const char *s, char *out, unsigned n) {
    int d = 0;
    while (s[d] >= '0' && s[d] <= '9') ++d;
    unsigned o = 0;
    for (int i = 0; i < d && o + 2 < n; ++i) {
        if (i && (d - i) % 3 == 0) out[o++] = ',';
        out[o++] = s[i];
    }
    tk_snprintf(out + o, n - o, "%s", s + d);
}

static int ab_is(const char *codec, const char *const *list) {
    for (; *list; ++list)
        if (!strcasecmp(codec, *list)) return 1;
    return 0;
}

/* The quality line from the details' Format, Bits, Sample Rate and Bit Rate values: a lossless
 * codec up to 16-bit and 48 kHz is Lossless, above either Hi-Res Lossless, its spec "FLAC · 16-bit
 * / 44.1 kHz"; a DSD file is DSD, its spec the rate's multiple of 44.1 kHz ("DSD64"), from the bit
 * rate (stereo: stock's sample rate for a DSF reads half the DSD rate); a lossy one has no badge,
 * its spec "MP3 · 320 kbps". */
static void ab_quality(void *win, const char *unk) {
    static const char *const lossless[] = { "FLAC", "ALAC", "WAV",     "AIFF", "AIF",
                                            "APE",  "WV",   "WAVPACK", 0 },
                             *const dsd[] = { "DSF", "DFF", "DSD", 0 };
    char codec[32], bits[32], rate[32], kbps[32];
    if (!ab_get(win, "label_encode_value", codec, sizeof codec, unk)) return;
    int has_bits = ab_get(win, "label_bits_value", bits, sizeof bits, unk),
        has_rate = ab_get(win, "label_sample_value", rate, sizeof rate, unk),
        has_kbps = ab_get(win, "label_kbps_value", kbps, sizeof kbps, unk);
    int b = has_bits ? ab_tenths(bits, 0) : -1, r = has_rate ? ab_tenths(rate, 0) : -1,
        k = has_kbps ? ab_tenths(kbps, 0) / 10 : -1;
    if (ab_is(codec, dsd)) {
        static const int mult[] = { 64, 128, 256, 512 };
        int m = 0, x = k > 0 ? (k * 10 + 441) / 882 : 0; /* kbps / (2 x 44.1) */
        for (int i = 1; i < 4; ++i)
            if ((x > mult[i] ? x - mult[i] : mult[i] - x) <
                (x > mult[m] ? x - mult[m] : mult[m] - x))
                m = i;
        ab.badge = BADGE_DSD;
        if (x)
            tk_snprintf(ab.t.spec, sizeof ab.t.spec, "DSD%d", mult[m]);
        else
            tk_snprintf(ab.t.spec, sizeof ab.t.spec, "%s", codec);
    } else if (ab_is(codec, lossless)) {
        ab.badge = b > 160 || r > 480 ? BADGE_HIRES : BADGE_LOSSLESS;
        tk_snprintf(ab.t.spec, sizeof ab.t.spec, "%s%s%s%s%s", codec, b > 0 || r > 0 ? DOT : "",
                    b > 0 ? bits : "", b > 0 && r > 0 ? " / " : "", r > 0 ? rate : "");
    } else {
        ab.badge = BADGE_NONE;
        tk_snprintf(ab.t.spec, sizeof ab.t.spec, "%s%s%s", codec, k > 0 ? DOT : "",
                    k > 0 ? kbps : "");
    }
}

/* Location in plain words, from the playing file's path: a card app's hidden folder
 * (/mnt/mmc/.<id>/) is the server's: a file straight in its stream/ folder is a stream the app
 * fetched for this play and drops after (q2-subsonic's uncached stream), any other its kept copy;
 * a synced song (the app's Subsonic/ folder) is the server's, downloaded; any other card file is
 * the SD card's, with its folder. 0 for a path that is none of these. */
static int ab_location(char *out, unsigned n) {
    const char *path = (const char *)g_play_id3_info, *rel;
    if (!path || !tk_str_start_with(path, "/mnt/mmc/")) return 0;
    rel = path + 9;
    if (*rel == '.') {
        const char *in = rel;
        while (*in && *in != '/') ++in;
        int stream = tk_str_start_with(in, "/stream/");
        for (const char *f = in + 8; stream && *f; ++f)
            if (*f == '/') stream = 0;
        tk_snprintf(out, n, "Server" DOT "%s", stream ? "Streaming" : "Downloaded");
    } else if (tk_str_start_with(rel, "Subsonic/")) {
        tk_snprintf(out, n, "Server" DOT "Downloaded");
    } else {
        const char *slash = (const char *)0;
        for (const char *c = rel; *c; ++c)
            if (*c == '/') slash = c;
        if (slash)
            tk_snprintf(out, n, "SD Card" DOT "%.*s", (int)(slash - rel), rel);
        else
            tk_snprintf(out, n, "SD Card");
    }
    return 1;
}

/* m:ss, or h:mm:ss from an hour. */
static void ab_clock(int s, char *out, unsigned n) {
    if (s >= 3600)
        tk_snprintf(out, n, "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60);
    else
        tk_snprintf(out, n, "%d:%02d", s / 60, s % 60);
}

/* ringnav_np_info's, after it wrote the details into win: the grid's header, quality line and
 * cells from them and Now Playing's own title, artist, length and cover, repainted when any
 * changed. */
void np_about_fill(void *win) {
    if (!ab.body || win != ab.page) return;
    static const char *const values[] = { "label_album_value", "label_year_value",
                                          "label_genre_value", "label_kbps_value",
                                          "label_size_value" };
    char unk[64] = "", v[160];
    getFormatString("player_unknown", unk, sizeof unk);
    memset(&ab.t, 0, sizeof ab.t);
    ab.badge = BADGE_NONE, ab.n = ab.wide = 0;
    if (st.np_title) widget_get_text_utf8(st.np_title, ab.t.title, sizeof ab.t.title);
    if (st.np_artist) widget_get_text_utf8(st.np_artist, ab.t.artist, sizeof ab.t.artist);
    int len = st.np_slider ? widget_get_prop_int(st.np_slider, "max", 0) : 0;
    if (len > 0) ab_clock(len, ab.t.len, sizeof ab.t.len);
    if (st.np_cover)
        tk_snprintf(ab.t.art, sizeof ab.t.art, "%s", widget_get_prop_str(st.np_cover, "image", ""));
    ab.t.art_key = st.np_art; /* the player writes each track's cover to the same file */
    ab_quality(win, unk);
    for (int i = 0; i < AB_CELLS; ++i) {
        ab_cell *c = &ab.t.cell[ab.n];
        char key[24];
        int known;
        if (i == CELL_LOCATION) {
            known = ab_location(c->value, sizeof c->value);
            tk_snprintf(key, sizeof key, "label_location");
        } else {
            known = ab_get(win, values[i], v, sizeof v, unk);
            tk_snprintf(key, sizeof key, "%.*s", (int)strlen(values[i]) - 6, values[i]);
            const char *rest = v;
            int t = known ? ab_tenths(v, &rest) : -1;
            if (i != CELL_ALBUM && i != CELL_GENRE && !t)
                known = 0; /* a 0 year, bit rate or size */
            if (i == CELL_KBPS)
                ab_thousands(v, c->value, sizeof c->value);
            else if (i == CELL_SIZE && t > 0)
                tk_snprintf(c->value, sizeof c->value, "%d.%d%s", t / 10, t % 10, rest);
            else
                tk_snprintf(c->value, sizeof c->value, "%s", v);
        }
        if (!known) continue;
        ab.wide = i == CELL_LOCATION;
        void *label = widget_lookup(win, key, 1);
        if (!label || widget_get_text_utf8(label, c->name, sizeof c->name)) *c->name = 0;
        ++ab.n;
    }
    /* the cells two a row, Location (always last) a row of its own */
    int pairs = ab.n - ab.wide;
    ab.end = AB_GRID_Y + ((pairs + 1) / 2 + ab.wide) * AB_ROW;
    unsigned key = hash_bytes(FNV_SEED, (const unsigned char *)&ab.t, sizeof ab.t);
    key = hash_bytes(key, (const unsigned char *)&ab.badge, sizeof ab.badge);
    int room = ab_room();
    if (ab.off > room) ab.off = room;
    if (key == ab.key) return;
    ab.key = key;
    widget_invalidate_force(ab.body, (void *)0);
    cap_ask();
}

/* The cover's corners rounded into the page's black: each corner row outside the arc, then its
 * edge pixel at the alpha of its uncovered part (navigation.c paint_cover's way). */
static void ab_corners(void *canvas, int x, int y, unsigned black) {
    for (int i = 0; i < AB_ART_R; ++i) {
        int out = arc_out(AB_ART_R, i), n = out >> 4;
        unsigned a = (unsigned)(out & 15) * 17;
        for (int c = 0; c < 4; ++c) {
            int row = c & 1 ? y + AB_ART - 1 - i : y + i, left = !(c & 2);
            if (n) scrub_row(canvas, 0xff000000u | black, left ? x : x + AB_ART - n, row, n);
            if (a) scrub_row(canvas, a << 24 | black, left ? x + n : x + AB_ART - n - 1, row, 1);
        }
    }
}

/* The grid, its screen rows less oy (the canvas's top on screen, less the scroll). */
static void ab_draw(void *w, void *canvas, int oy) {
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), bm[64];
    int light = ipod_light(), y = AB_HEAD_Y - oy, right = 375 - geom_val(V_RIGHT_X);
    unsigned black = flip_if(light, 0);
    if (*ab.t.art && !widget_load_image(w, ab.t.art, bm) && bm[0] && bm[1]) {
        rect_t src = { 0, 0, (int)bm[0], (int)bm[1] }, dst = { AB_ART_X, y, AB_ART, AB_ART };
        canvas_draw_image(canvas, bm, &src, &dst);
        ab_corners(canvas, AB_ART_X, y, black);
    } else {
        rect_t r = { AB_ART_X, y, AB_ART, AB_ART };
        for (int i = 0; i < AB_ART; ++i)
            round_row(canvas, &r, AB_ART_R, i, 0xff000000u | flip_if(light, AB_MONO));
    }
    stock_canvas_set_fill_color(canvas, fill);
    int tw = right - AB_TEXT_X;
    ar_text(canvas, ab.t.title, 1, PX_20, 0xffffff, 0, AB_TEXT_X, y + AB_TITLE_DY, tw, 2);
    ar_text(canvas, ab.t.artist, 0, PX_15, AB_SECOND, 0, AB_TEXT_X, y + AB_ARTIST_DY, tw, 2);
    ar_text(canvas, ab.t.len, 0, PX_13, AB_THIRD, 0, AB_TEXT_X, y + AB_LEN_DY, tw, 2);
    int qy = AB_QUALITY_Y - oy, sx = AB_ART_X;
    if (ab.badge) {
        static const char *const badges[] = { "", "Lossless", "Hi-Res Lossless", "DSD" };
        int bw = ar_text(canvas, badges[ab.badge], 1, PX_13, 0xffffff, 0, AB_ART_X + AB_BADGE_TEXT,
                         qy + AB_BADGE_H / 2, 200, 2) +
                 AB_BADGE_PAD;
        rect_t r = { AB_ART_X, qy, bw, AB_BADGE_H };
        round_ring(canvas, &r, AB_BADGE_R, 0xff000000u | flip_if(light, AB_SECOND));
        paint_art(w, canvas, theme_art(light ? "quality_l" : "quality_d"),
                  AB_ART_X + AB_GLYPH_DX - AB_GLYPH / 2, qy + (AB_BADGE_H - AB_GLYPH) / 2);
        stock_canvas_set_fill_color(canvas, fill);
        sx += bw + AB_SPEC_GAP;
    }
    ar_text(canvas, ab.t.spec, 0, PX_13, AB_THIRD, 0, sx, qy + AB_BADGE_H / 2, right - sx, 2);
    for (int i = 0, slot = 0; i < ab.n; ++i) {
        const ab_cell *c = &ab.t.cell[i];
        int wide = ab.wide && i == ab.n - 1;
        if (wide && slot & 1) ++slot; /* Location starts a row of its own */
        int cx = slot & 1 ? AB_COL2 : AB_ART_X, cy = AB_GRID_Y + slot / 2 * AB_ROW - oy;
        ar_text(canvas, c->name, 0, PX_13, AB_THIRD, 0, cx, cy + AB_NAME_DY, AB_VALUE_W, 2);
        ar_text(canvas, c->value, 0, PX_17, 0xffffff, 0, cx, cy + AB_VALUE_DY,
                wide ? right - cx : AB_VALUE_W, 2);
        ++slot;
    }
}

/* ringnav_paint's, after stock painted w: the grid on its view. */
void np_about_paint(void *w, void *canvas) {
    if (!w || w != ab.body || !P(canvas, CANVAS_LCD)) return;
    ab_draw(w, canvas, I(ab.page, W_Y) + I(w, W_Y) + ab.off);
}

/* The status bar's background (navigation.c ringnav_paint_bg): the header's top, which stands
 * above the window, while the page is on top and still, at its top: scrolled, the window clips
 * the grid at its own top. The bar's own paint clips it to the bar. */
void np_about_cap(void *bar, void *canvas) {
    void *wm = window_manager();
    if (!ab.body || ab.off || !P(canvas, CANVAS_LCD) ||
        window_manager_get_top_window(wm) != ab.page || window_manager_is_animating(wm))
        return;
    ab_draw(bar, canvas, I(bar, W_Y));
}
