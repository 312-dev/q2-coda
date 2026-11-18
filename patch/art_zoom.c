/* The cover grows into Now Playing (docs/oled-design.md#motion): opened from a page that shows the
 * playing song's cover (Home's art pane, a dressed album page's header), Now Playing's art flies
 * from that cover's place to its own while the page fades out over the run's first half and Now
 * Playing fades in from AZ_IN_FROM on, AZ_OPEN_MS cubic_out. Return runs it back over
 * AZ_CLOSE_MS, shrinking the art to where it came from, when the page under is still the one it
 * came from; over another it is a plain cross-fade. Opened anywhere else, Now Playing opens at once
 * as before. Under Reduce Motion both are a MO_FADE_MS cross-fade (AWTK's fade animator).
 *
 * art_zoom_load, as Now Playing's window loads (nav/home_carousel.c motion_load), finds the cover
 * on the window under it, the page it opens over (art_zoom_source), and if there is one gives the
 * window an open_anim_hint and a close_anim_hint naming AZ_TYPE, an animator registered with
 * stock's window animator factory on first use. It is stock's own window_animator_t
 * (window_animator_create) with a vtable of its own: stock's htranslate update (the eased percent,
 * reversed for a close) and two draws over its snapshots, the page (prev_img) on the ground at its
 * alpha, then Now Playing (curr_img) at its own and its art's square from that snapshot at the
 * place between the two covers the percent gives.
 */
#include "navigation.h"

#define AZ_TYPE "art_zoom"
#define AZ_OPEN_MS 300
#define AZ_CLOSE_MS 240
#define AZ_OUT_TO 500  /* the page is gone at this share of the run, thousandths */
#define AZ_IN_FROM 400 /* Now Playing starts in at this share */
/* window_animator_t: prev_win @0x14, prev_img @0x1c, curr_img @0x64, percent @0xb4 (float),
 * canvas @0xb8; its vtable: type, desc, size, overlap, init, update_percent, draw_prev_window,
 * draw_curr_window, destroy (stock's htranslate's at 0x93b234) */
#define WA_PREV_WIN 0x14
#define WA_PREV_IMG 0x1c
#define WA_CURR_IMG 0x64
#define WA_PERCENT 0xb4
#define WA_CANVAS 0xb8
#define WA_SIZE 0xc8

static struct {
    void *from;   /* the page Now Playing opened over with a cover; 0 none */
    rect_t cover; /* that cover on screen */
    int registered;
} az __attribute__((section(".scratch")));

/* win's open_anim_hint or close_anim_hint, "" when it has none. Stock's window_base_get_prop answers
 * these itself with the window's own field, which is NULL until one is set, and widget_get_prop_str
 * passes that NULL on instead of its default: a window loaded new (Now Playing over a list, the
 * first time) has none. */
const char *window_hint(void *win, const char *name) {
    const char *hint = widget_get_prop_str(win, name, "");
    return hint ? hint : "";
}

/* w's box on screen */
static rect_t screen_rect(void *w) {
    rect_t r = { 0, 0, I(w, W_W), I(w, W_H) };
    for (void *p = w; p && p != window_manager(); p = P(p, W_PARENT))
        r.x += I(p, W_X), r.y += I(p, W_Y);
    return r;
}

/* The playing song's cover on top, the page Now Playing is opening over, into r: the middle square
 * of Home's art pane (the cover of the row it opened from: Now Playing's), or a dressed album
 * page's header. */
int art_zoom_source(void *top, rect_t *r) {
    void *art = coverflow_home_pane();
    if (top && art && window_of(art) == top && widget_get_visible(art)) {
        rect_t pane = screen_rect(art); /* the cover fills it, cropped: its middle square */
        int side = pane.w < pane.h ? pane.w : pane.h;
        r->x = pane.x + (pane.w - side) / 2, r->y = pane.y + (pane.h - side) / 2,
        r->w = r->h = side;
        return side > 0;
    }
    return top && album_local_on(top) && hero_cover_box(r);
}

/* At thousandths k of the run: the page's alpha, Now Playing's, and the art's box from a to b. */
void art_zoom_at(int k, const rect_t *a, const rect_t *b, unsigned *page, unsigned *np,
                 rect_t *art) {
    k = k < 0 ? 0 : k > 1000 ? 1000 : k;
    *page = k >= AZ_OUT_TO ? 0 : 255u * (unsigned)(AZ_OUT_TO - k) / AZ_OUT_TO;
    *np = k <= AZ_IN_FROM ? 0 : 255u * (unsigned)(k - AZ_IN_FROM) / (1000 - AZ_IN_FROM);
    art->x = a->x + (b->x - a->x) * k / 1000, art->y = a->y + (b->y - a->y) * k / 1000;
    art->w = a->w + (b->w - a->w) * k / 1000, art->h = a->h + (b->h - a->h) * k / 1000;
}

static int az_k(void *wa) { return (int)(*(float *)((char *)wa + WA_PERCENT) * 1000 + 0.5f); }

static int az_update(void *wa) { return htranslate_update(wa); }

/* The page on the ground, fading out. */
static int az_prev(void *wa) {
    void *c = P(wa, WA_CANVAS), *lcd = c ? P(c, CANVAS_LCD) : (void *)0;
    if (!lcd) return 0;
    rect_t all = { 0, 0, I(lcd, LCD_W), I(lcd, LCD_H) }, art;
    unsigned page, np, fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    art_zoom_at(az_k(wa), &all, &all, &page, &np, &art);
    stock_canvas_set_fill_color(c, 0xff000000u | (ipod_light() ? scheme()->paper : 0));
    canvas_fill_rect(c, 0, 0, all.w, all.h);
    stock_canvas_set_fill_color(c, fill);
    canvas_set_global_alpha(c, page);
    if (page) canvas_draw_image(c, (char *)wa + WA_PREV_IMG, &all, &all);
    canvas_set_global_alpha(c, 255);
    return 0;
}

/* Now Playing fading in, its art flying between the cover's place and its own: from the page it
 * came from, a fade only. */
static int az_curr(void *wa) {
    void *c = P(wa, WA_CANVAS), *lcd = c ? P(c, CANVAS_LCD) : (void *)0;
    if (!lcd) return 0;
    rect_t all = { 0, 0, I(lcd, LCD_W), I(lcd, LCD_H) }, to, art;
    unsigned page, np;
    int k = az_k(wa), flies = az.from && P(wa, WA_PREV_WIN) == az.from && st.np_cover;
    if (flies) {
        int b[4]; /* the art as it stands, smaller when paused (np_settle.c) */
        to = screen_rect(st.np_cover);
        np_settle_box(st.np_cover, b);
        to.x += b[0], to.y += b[1], to.w = b[2], to.h = b[3];
    }
    art_zoom_at(k, flies ? &az.cover : &all, flies ? &to : &all, &page, &np, &art);
    canvas_set_global_alpha(c, np);
    if (np) canvas_draw_image(c, (char *)wa + WA_CURR_IMG, &all, &all);
    canvas_set_global_alpha(c, 255);
    if (flies && k < 1000) canvas_draw_image(c, (char *)wa + WA_CURR_IMG, &to, &art);
    return 0;
}

static const struct {
    const char *type, *desc;
    unsigned size;
    int overlap;
    int (*init)(void *), (*update)(void *), (*prev)(void *), (*curr)(void *);
    void (*destroy)(void *);
} az_vt = { AZ_TYPE, AZ_TYPE, WA_SIZE, 0, 0, az_update, az_prev, az_curr, 0 };

static void *az_create(int open, void *args) {
    (void)args;
    return window_animator_create(open, &az_vt);
}

/* Now Playing's window as it loads: from a cover, its open and close take AZ_TYPE (a fade under
 * Reduce Motion); else its open stays immediate and its close the theme's (motion_hint). */
void art_zoom_load(void *np_win) {
    void *wm = window_manager(), *top = (void *)0;
    for (int i = (int)widget_count_children(wm) - 1, below = 0; i >= 0 && !top; --i) {
        void *w = widget_get_child(wm, (unsigned)i); /* the window under Now Playing, which is in */
        if (w == np_win)                             /* the stack as it loads */
            below = 1;
        else if (below && !tk_strcmp(widget_get_type(w), "window"))
            top = w;
    }
    az.from = art_zoom_source(top, &az.cover) ? top : (void *)0;
    if (!az.from) { /* a reused window's last open: immediate again */
        if (window_hint(np_win, "open_anim_hint")[0])
            widget_set_prop_str(np_win, "open_anim_hint", "");
        return;
    }
    int reduced = motion_reduced();
    if (!reduced && !az.registered)
        az.registered =
            !window_animator_factory_register(window_animator_factory(), AZ_TYPE, az_create);
    widget_set_prop_str(np_win, "open_anim_hint",
                        reduced ? "fade(duration=150)" : "art_zoom(duration=300)");
    widget_set_prop_str(np_win, "close_anim_hint",
                        reduced ? "fade(duration=150)" : "art_zoom(duration=240)");
    _Static_assert(MO_FADE_MS == 150 && AZ_OPEN_MS == 300 && AZ_CLOSE_MS == 240,
                   "the hints' durations");
}
