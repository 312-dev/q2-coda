/* Cover Flow with no albums (docs/oled-design.md#empty-and-offline): the house empty page, by what
 * is missing, in place of stock's "Update Local Music first": three faint album squares with a
 * glyph on the middle one, a title, a hint and, where one fixes it, one selected action row.
 *   CF_UNSCANNED  music on the card, none in the library: "No Albums Yet", "Music on the card isn't
 *                 scanned", Scan Music Now (stock's scan, its own dialog)
 *   CF_STREAMING  no card music, a catalogue app signed in (catalogue.c catalogue_signed) while
 *                 Streaming Songs is off: "No Albums Here Yet", "Your <app> albums can show here",
 *                 Show Streaming Songs (library_settings.c library_needs, which asks first); with
 *                 Streaming Songs on the same words, no row, while its albums are on their way
 *   CF_NONE       neither: "No Albums", "Copy music to the SD card", no row
 * The page paints its art and words itself (cf_empty_paint, on a view over the page's body); the
 * row is a page_list row the wheel and Centre reach as any. coverflow.c builds it and loads the
 * covers again once the library changes. */
#include "navigation.h"

enum { CF_NONE, CF_STREAMING, CF_UNSCANNED };
#define CF_EMPTY_NAME "_cf_empty"
#define CF_ART_Y 92 /* the squares' centre, a screen y, as the title's, the hint's and the row's   \
                     */
#define CF_TITLE_Y 150
#define CF_HINT_Y 174
#define CF_ROW_Y 206
#define CF_SQUARE 64 /* the middle square; the two behind it 54, 34 px either side */
#define CF_SIDE 54
#define CF_SIDE_DX 34
#define CF_SQUARE_R 8
#define CF_GLYPH 30      /* tools/art.py CF_EMPTY_PX */
#define CF_TITLE_PX 24   /* Inter Medium 20 px, AWTK's size */
#define CF_HINT_PX 18    /* Inter 15 px */
#define CF_WALK_MAX 4000 /* card entries looked at for a song, at most */
#define CF_WALK_DEPTH 6

static struct {
    int cause;
    char app[32]; /* CF_STREAMING's app, by its q2app name */
} ce __attribute__((section(".scratch")));

static int audio_name(const char *d) {
    static const char *const exts[] = { "flac", "mp3", "m4a", "aac", "wav", "ape",  "ogg",
                                        "opus", "dsf", "dff", "wma", "aif", "aiff", "wv" };
    const char *dot = strrchr(d, '.');
    for (unsigned i = 0; dot && i < sizeof exts / sizeof *exts; ++i)
        if (!strcasecmp(dot + 1, exts[i])) return 1;
    return 0;
}
/* A song file under path (a folder of n bytes in a 512 byte buffer), dot-folders left out, at most
 * CF_WALK_DEPTH deep and *budget entries in all. */
static int walk(char *path, unsigned n, int depth, int *budget) {
    void *dir = opendir(path);
    int found = 0;
    for (struct dirent *e; dir && !found && *budget > 0 && (e = readdir(dir));) {
        const char *d = e->d_name;
        --*budget;
        if (d[0] == '.') continue;
        if (e->d_type == 8 || !e->d_type) found = audio_name(d);
        if (!found && (e->d_type == 4 || !e->d_type) && depth < CF_WALK_DEPTH &&
            n + 1 + strlen(d) < 511) {
            tk_snprintf(path + n, 512 - n, "/%s", d);
            found = walk(path, n + 1 + strlen(d), depth + 1, budget);
            path[n] = 0;
        }
    }
    if (dir) closedir(dir);
    return found;
}
static int card_has_music(void) {
    char path[512] = "/mnt/mmc";
    int budget = CF_WALK_MAX;
    return walk(path, strlen(path), 0, &budget);
}

/* The empty page's row: Scan Music Now or Show Streaming Songs. coverflow.c's poll loads the
 * covers again once either has changed the library. */
static int row_click(void *ctx, void *event) {
    if (ce.cause == CF_UNSCANNED) {
        int scan[4] = { 0 };
        navigator_to_with_context("dialog/updatemusic_dialog", scan);
    } else if (ce.cause == CF_STREAMING)
        library_needs();
    return 0;
}

/* Cover Flow's page with no albums under a theme with the house's empty pages (OLED's
 * shuffle_row): built into body, 1; 0 under another theme, which keeps its own words. */
int cf_empty(void *page, void *body) {
    if (!theme()->shuffle_row) return 0;
    char id[16];
    int signed_in = catalogue_signed(id, ce.app, sizeof ce.app);
    ce.cause = card_has_music() ? CF_UNSCANNED : signed_in ? CF_STREAMING : CF_NONE;
    int row = ce.cause == CF_UNSCANNED || (ce.cause == CF_STREAMING && !catalogue_on());
    void *view = page_list(page, body, 0, "Cover Flow", row, 48);
    int y = 0;
    for (void *w = body; w && w != window_manager(); w = P(w, W_PARENT)) y += I(w, W_Y);
    if (row) {
        page_row_detail(view, 0,
                        ce.cause == CF_UNSCANNED ? "Scan Music Now" : "Show Streaming Songs", 0,
                        row_click);
        void *list = P(view, W_PARENT);
        widget_move_resize(list, I(list, W_X), CF_ROW_Y - y, I(list, W_W), page_h(48));
    }
    void *art =
        widget_factory_create_widget(widget_factory(), "view", body, 0, 0, 375, I(body, W_H));
    widget_set_name(art, CF_EMPTY_NAME);
    widget_restack(art, 0); /* under the row */
    return 1;
}

/* ringnav_paint_bg: the empty page's art and words, on its view (local coordinates, from the
 * screen's). */
void cf_empty_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) ||
        tk_strcmp(widget_get_prop_str(w, "name", ""), CF_EMPTY_NAME))
        return;
    int oy = 0, light = ipod_light();
    for (void *p = w; p && p != window_manager(); p = P(p, W_PARENT)) oy += I(p, W_Y);
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    static const signed char dx[] = { -CF_SIDE_DX, CF_SIDE_DX, 0 };
    for (int k = 0; k < 3; ++k) {
        int s = k < 2 ? CF_SIDE : CF_SQUARE;
        rect_t r = { 375 / 2 + dx[k] - s / 2, CF_ART_Y - oy - s / 2, s, s };
        fill_box(canvas, &r, RGBA(flip_if(light, k < 2 ? 0x101011 : 0x3a3a3c)), CF_SQUARE_R);
        round_ring(canvas, &r, CF_SQUARE_R, RGBA(flip_if(light, k < 2 ? 0x161617 : 0x505054)));
    }
    stock_canvas_set_fill_color(canvas, fill);
    paint_art(w, canvas, theme_art(ce.cause == CF_STREAMING ? "cfempty_cloud" : "cfempty_music"),
              375 / 2 - CF_GLYPH / 2, CF_ART_Y - oy - CF_GLYPH / 2);
    char hint[64];
    const char *title = ce.cause == CF_UNSCANNED   ? "No Albums Yet"
                        : ce.cause == CF_STREAMING ? "No Albums Here Yet"
                                                   : "No Albums";
    if (ce.cause == CF_STREAMING)
        tk_snprintf(hint, sizeof hint, "Your %s albums can show here", ce.app);
    else
        tk_snprintf(hint, sizeof hint, "%s",
                    ce.cause == CF_UNSCANNED ? "Music on the card isn't scanned"
                                             : "Copy music to the SD card");
    ar_text(canvas, title, 1, CF_TITLE_PX, flip_if(light, 0xffffff), 0, 375 / 2, CF_TITLE_Y - oy,
            375 - 48, 1);
    ar_text(canvas, hint, 0, CF_HINT_PX, light ? 0x5c5c5c : 0xababab, 0, 375 / 2, CF_HINT_Y - oy,
            375 - 48, 1);
}
