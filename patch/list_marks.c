/* A theme's own list marks and status glyphs (docs/oled-design.md): the chevron from its own art
 * on every row that opens a page (chevron_icon, spinner_icon), the scroll indicator that shows
 * while the wheel turns (wheel_sb_alpha), the slim battery (battery_slim), a switch (switch_mark),
 * a scrolling label's faded ends (marquee_fade), and the row editors' grip (grip_mark), lifted
 * row's card (lift_card_paint) and check circle (check_circle). Each draws in final colours, its
 * own in each half. */
#include "navigation.h"

/* CHEV_ICON: the image's centre this far left of the right column, as the design places the
 * caret's 15 px box (tools/art.py draws the caret-right glyph in it) */
#define CHEV_SHIFT 5
#define SPIN_BOX 16 /* the spinner's 15 px frame (tools/art.py spinner), whole */

/* SB_WHEEL: shown while the wheel turns and for UI_SHOW_MS after its last tick (ui_kit.h's
 * show-then-fade). */

static unsigned sb_at __attribute__((section(".scratch")));    /* the last tick, time_now_ms */
static void *sb_list __attribute__((section(".scratch")));     /* the list it moved */
static void *sb_owner __attribute__((section(".scratch")));    /* ... and its list view */
static unsigned sb_timer __attribute__((section(".scratch"))); /* the fade's next repaint */
static char art_name[48] __attribute__((section(".scratch"))); /* theme_art's answer */

/* The active theme's own image name: its art prefix (tools/art.py) then name. The answer lasts
 * until the next call. */
const char *theme_art(const char *name) {
    tk_snprintf(art_name, sizeof art_name, "%s%s", theme()->art, name);
    return art_name;
}

/* The `>` of a row whose centre row is y, its glyph on the set's right column, whose screen x the
 * canvas reaches at right (canvas columns): white on the selection, else the half's grey. */
void chevron_icon(void *w, void *canvas, int right, int y, int selected) {
    unsigned bitmap[64]; /* bitmap_t */
    const char *name = theme_art(selected ? "chevron_w" : ipod_light() ? "chevron_l" : "chevron_d");
    if (!widget_load_image(w, name, bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
}

/* The activity spinner (tools/art.py spinner: eight spokes, the bright one a step clockwise each
 * SPIN_FRAME_MS), one for every list that shows one: Home's Cover Flow row while it opens, and
 * Settings > Bluetooth's connecting row and Other Devices' scan. Its frame is the clock's, so no
 * painter keeps a count, and a painter says where it drew it (spinner_painted): one timer,
 * every MO_FRAME_MS while any spinner shows, repaints those boxes as the frame changes, and stops
 * once none was painted for two frames. A box is its widget's and goes with it (EVT_DESTROY). */
#define SPIN_FRAME_MS 100
#define SPIN_BOXES 4
static struct {
    struct {
        void *w;           /* the widget it was painted on, 0 a free slot */
        rect_t r;          /* the box in w; w's whole when r.w is 0 */
        unsigned frame;    /* the clock's frame it was last painted at */
        unsigned char ask; /* its repaint asked for since */
    } at[SPIN_BOXES];
    unsigned timer;
} spin __attribute__((section(".scratch")));
static unsigned spin_clock(void) { return (unsigned)time_now_ms() / SPIN_FRAME_MS; }
int spinner_frame(void) { return (int)(spin_clock() % 8); }
static int spin_gone(void *ctx, void *event) {
    (void)event;
    for (int i = 0; i < SPIN_BOXES; ++i)
        if (spin.at[i].w == ctx) spin.at[i].w = (void *)0;
    return 0;
}
/* Each box whose frame is due asked to repaint: 2 when one was, 1 when a spinner shows and none is
 * due yet, 0 when none shows. */
static int spinner_turn(void) {
    unsigned now = spin_clock();
    int live = 0;
    for (int i = 0; i < SPIN_BOXES; ++i) {
        __typeof__(spin.at[0]) *b = &spin.at[i];
        if (!b->w || now - b->frame > 2) continue; /* not painted lately: not shown */
        if (!live) live = 1;
        if (b->ask || now == b->frame) continue;
        b->ask = 1, live = 2;
        widget_invalidate_force(b->w, b->r.w ? &b->r : (rect_t *)0);
    }
    return live;
}
static int spin_tick(const void *info) {
    (void)info;
    if (spinner_turn()) return 8; /* RET_REPEAT */
    spin.timer = 0;
    return 7; /* RET_REMOVE */
}
void spinner_painted(void *w, const rect_t *r) {
    int k = -1;
    unsigned now = spin_clock();
    for (int i = 0; i < SPIN_BOXES && k < 0; ++i)
        if (spin.at[i].w == w) k = i;
    for (int i = 0; i < SPIN_BOXES && k < 0; ++i)
        if (!spin.at[i].w) k = i;
    for (int i = 0; i < SPIN_BOXES && k < 0; ++i) /* full: the one painted longest ago */
        if (now - spin.at[i].frame > 2) k = i;
    if (k < 0) return;
    if (spin.at[k].w != w) widget_on(w, EVT_DESTROY, spin_gone, w);
    spin.at[k].w = w, spin.at[k].frame = now, spin.at[k].ask = 0;
    spin.at[k].r = r ? *r : (rect_t){ 0, 0, 0, 0 };
    if (!spin.timer) spin.timer = timer_add(spin_tick, (void *)0, MO_FRAME_MS);
}

/* The loading wait (docs/internals.md#loading-wait, docs/oled-design.md#loading): one look for
 * every action that has to load before its result is on screen. The busy spinner's form (a #1C1C1E
 * square with 12 px corners, eight 7 px dots 15 px out, 11 on the diagonals, the lit one a step
 * clockwise each SPIN_FRAME_MS and each before it dimmer) centred on the screen (375 x 320, the
 * status row over it) and, when the wait names what it loads, that caption under it: Inter 15 px
 * in Text Secondary on a #1C1C1E plate LOAD_CAP_H tall, LOAD_CAP_GAP under the square, ending in
 * an ellipsis past LOAD_CAP_MAX; the same in every theme. It shows only once a wait has lasted
 * LOAD_SHOW_MS, so a quick action never flashes it. load_draw paints it on canvas with the screen's
 * (ox, oy) at the canvas's origin: a window's own canvas (0, 0: every page takes the whole screen,
 * docs/oled-design.md#full-bleed), or load_sync_begin's off-screen one (f -1: no dots, which its
 * painter puts on). */
#define LOAD_SPIN_R 12
#define LOAD_DOT 7
#define LOAD_DOT_OUT 15
#define LOAD_SPIN_X (375 / 2 - LOAD_SPIN_BOX / 2)
#define LOAD_SPIN_Y (320 / 2 - LOAD_SPIN_BOX / 2)
#define LOAD_CAP_GAP 8
#define LOAD_CAP_H 28
#define LOAD_CAP_PAD 14
#define LOAD_CAP_MAX 240
#define LOAD_CAP_Y (LOAD_SPIN_Y + LOAD_SPIN_BOX + LOAD_CAP_GAP)
/* the screen's box the overlay may cover, caption included */
#define LOAD_AREA_X (375 / 2 - LOAD_CAP_MAX / 2)
#define LOAD_AREA_W LOAD_CAP_MAX
#define LOAD_AREA_H (LOAD_CAP_Y + LOAD_CAP_H - LOAD_SPIN_Y)
static void load_draw(void *canvas, int ox, int oy, int f, const char *caption) {
    static const signed char dots[8][2] = { { 0, -1 }, { 1, -1 }, { 1, 0 },  { 1, 1 },
                                            { 0, 1 },  { -1, 1 }, { -1, 0 }, { -1, -1 } };
    static const unsigned char lit[8] = { 255, 200, 160, 128, 100, 80, 64, 56 };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int cx = LOAD_SPIN_X + LOAD_SPIN_BOX / 2 - ox, cy = LOAD_SPIN_Y + LOAD_SPIN_BOX / 2 - oy;
    rect_t box = { LOAD_SPIN_X - ox, LOAD_SPIN_Y - oy, LOAD_SPIN_BOX, LOAD_SPIN_BOX };
    fill_box(canvas, &box, RGBA(RGB_RAISED), LOAD_SPIN_R);
    for (int k = 0; f >= 0 && k < 8; ++k) {
        int out = dots[k][0] && dots[k][1] ? 11 : LOAD_DOT_OUT;
        unsigned g = lit[(f - k) & 7];
        rect_t d = { cx + dots[k][0] * out - LOAD_DOT / 2, cy + dots[k][1] * out - LOAD_DOT / 2,
                     LOAD_DOT, LOAD_DOT };
        fill_box(canvas, &d, RGBA(g << 16 | g << 8 | g), LOAD_DOT / 2);
    }
    if (caption && *caption) {
        int px = TYPE(15), w = ui_text_w(canvas, caption, 0, px) + 2 * LOAD_CAP_PAD;
        if (w > LOAD_CAP_MAX) w = LOAD_CAP_MAX;
        rect_t plate = { 375 / 2 - w / 2 - ox, LOAD_CAP_Y - oy, w, LOAD_CAP_H };
        fill_box(canvas, &plate, RGBA(RGB_RAISED), LOAD_SPIN_R);
        ui_text(canvas, caption, 0, px, RGB_SECONDARY, 1, 375 / 2 - ox,
                LOAD_CAP_Y + LOAD_CAP_H / 2 - oy, w - 2 * LOAD_CAP_PAD, 1);
    }
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}
/* A wait's paints on window w: shown from LOAD_SHOW_MS after the first, a gap of more than two
 * frames starting the count again (a new wait). Each paint asks the shared timer for the next
 * frame's, shown or not, so the spinner appears on time and turns. */
static struct {
    void *w;
    unsigned since, clock;
} ld __attribute__((section(".scratch")));
static void load_paint(void *w, void *canvas, const char *caption) {
    if (!w || !canvas || !P(canvas, CANVAS_LCD)) return;
    unsigned now = (unsigned)time_now_ms(), c = spin_clock();
    if (w != ld.w || c - ld.clock > 2) ld.w = w, ld.since = now;
    ld.clock = c;
    if (now - ld.since >= LOAD_SHOW_MS) load_draw(canvas, 0, 0, spinner_frame(), caption);
    rect_t area = { LOAD_AREA_X, LOAD_SPIN_Y, LOAD_AREA_W, LOAD_AREA_H },
           box = { LOAD_SPIN_X, LOAD_SPIN_Y, LOAD_SPIN_BOX, LOAD_SPIN_BOX };
    spinner_painted(w, caption && *caption ? &area : &box);
}
/* The spinner alone over window w, for a page's own overlay (a card app's, Radio's, Spotify's). */
void load_spinner_paint(void *w, void *canvas) { load_paint(w, canvas, 0); }
int load_spinner_hit(int x, int y) {
    return x >= LOAD_SPIN_X && x < LOAD_SPIN_X + LOAD_SPIN_BOX && y >= LOAD_SPIN_Y &&
           y < LOAD_SPIN_Y + LOAD_SPIN_BOX;
}

/* A touch on window page while its loading overlay shows (any but the abort): taken from the rows
 * under it; at the lift a row it pressed is let go, unclicked, and a lift off the spinner calls
 * cancel. RET_STOP. */
int load_touch(void *page, void *event, void (*cancel)(void)) {
    if (I(event, EVENT_TYPE) == EVT_POINTER_UP_BEFORE) {
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, page, I(event, EVENT_X),
                           I(event, EVENT_Y));
        widget_dispatch_event_to_target_recursive(page, abort_event);
        if (!load_spinner_hit(I(event, EVENT_X), I(event, EVENT_Y))) cancel();
    }
    return 11; /* RET_STOP */
}

/* The wait over a window (load_wait): the window an action was taken on, win, stays as it is under
 * the overlay until the work is done, as Cover Flow's open holds Home (coverflow.c
 * coverflow_wait_input). step, when there is one, is the work's slice on the UI thread, or its look
 * at a worker, every LOAD_STEP_MS until it answers 1 (done: the overlay goes; the step has put the
 * result on screen); without one the caller ends the wait itself (load_over(0, 0): a page opened
 * once its content came). Painted by ringnav_paint (load_over_paint) and fed every input first by
 * ringnav_input (load_over_input) from its start, shown or not yet: the wheel's Centre and Return
 * go nowhere, nor do touches, so nothing is asked twice; Return's release, or a lift off the
 * spinner, gives the wait up (cancel: the work stopped, or its result to be dropped). So does win
 * going away (EVT_DESTROY). One at a time: a second takes the first's place, the first cancelled.
 */
#define LOAD_STEP_MS 16 /* MO_FRAME_MS */
static struct {
    void *win;
    void (*cancel)(void);
    int (*step)(void);
    const char *caption;
    unsigned timer;
} lo __attribute__((section(".scratch")));
static int lo_gone(void *ctx, void *event);
static void lo_end(void) {
    if (lo.timer) timer_remove(lo.timer);
    lo.timer = 0;
    if (!lo.win) return;
    widget_off_by_func(lo.win, EVT_DESTROY, (void *)lo_gone, lo.win);
    widget_invalidate_force(lo.win, 0);
    lo.win = 0, lo.cancel = 0, lo.step = 0, lo.caption = 0;
}
/* The overlay given up: ended, then its cancel called. */
static void lo_cancel(void) {
    void (*cancel)(void) = lo.cancel;
    lo_end();
    if (cancel) cancel();
}
static int lo_gone(void *ctx, void *event) {
    (void)event;
    if (ctx == lo.win) lo_cancel();
    return 0;
}
static int lo_tick(const void *unused) {
    (void)unused;
    if (!lo.step || !lo.step()) return 8; /* RET_REPEAT */
    lo.timer = 0;                         /* this timer goes with its RET_REMOVE */
    lo_end();
    return 7; /* RET_REMOVE */
}
/* The wait over win for work that runs on through step (above), caption what it loads ("Shuffling
 * songs…", 0 for none); win 0 ends the wait, cancelling nothing. */
void load_wait(void *win, const char *caption, int (*step)(void), void (*cancel)(void)) {
    if (lo.win && win) lo_cancel();
    lo_end();
    if (!win) return;
    lo.win = win, lo.cancel = cancel, lo.step = step, lo.caption = caption;
    widget_on(win, EVT_DESTROY, lo_gone, win);
    widget_invalidate_force(win, 0);
    if (step) lo.timer = timer_add(lo_tick, (void *)0, LOAD_STEP_MS);
}
/* The wait for a page that opens once its content is there (a card app's first, Chapters); the
 * caller ends it. */
void load_over(void *win, void (*cancel)(void)) { load_wait(win, 0, 0, cancel); }
/* Whether the overlay shows for the load whose cancel is cancel. */
int load_over_for(void (*cancel)(void)) { return lo.win && lo.cancel == cancel; }
/* Whether any wait holds a window (load_wait). */
int load_waiting(void) { return lo.win != 0; }
void load_over_paint(void *w, void *canvas) {
    if (lo.win && w == lo.win) load_paint(w, canvas, lo.caption);
}
/* ringnav_input, every event first: 1 when e goes nowhere while the overlay shows. Pointer events
 * here are the panel's own (portrait: the screen's x is its y, the screen's y 319 less its x). */
int load_over_input(void *e) {
    if (!lo.win || !e) return 0;
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    if (type == EVT_KEY_DOWN || type == EVT_KEY_LONG || type == EVT_KEY_UP) {
        if (key != KEY_RETURN && key != KEY_CENTER) return 0; /* the wheel and Play stay theirs */
        if (key == KEY_RETURN && type == EVT_KEY_UP) lo_cancel();
        return 1;
    }
    if (type < EVT_POINTER_FIRST || type >= EVT_POINTER_ABORT) return 0;
    if (type == EVT_POINTER_UP_BEFORE - 1 && /* the lift */
        !load_spinner_hit(I(e, EVENT_Y), 319 - I(e, EVENT_X)))
        lo_cancel();
    return 1;
}

/* The same wait where the work is one call on the UI thread that it cannot slice, leave or abandon
 * (stock's library query under Shuffle Songs, a Library list's sort under load_localclass_list):
 * load_sync_begin before it, load_sync_end after. No frame is painted while the call runs, so the
 * overlay goes straight onto the shown framebuffer page (/dev/fb0 at its FBIOGET_VSCREENINFO y
 * offset; 320 x 375, 32 bpp, the panel's x the screen's 319 - y and its y the screen's x). The UI
 * thread draws it once, over what the framebuffer shows in LOAD_AREA, through an off-screen canvas
 * with no dots (plate); a thread of its own (fb_painter), which touches only these buffers and the
 * framebuffer, waits LOAD_SHOW_MS and then puts the plate and the frame's dots there every
 * LOAD_TICK_MS until the end, which joins it and puts back what it covered. A call done within
 * LOAD_SHOW_MS never shows it. Nothing cancels the call: no input is read while it runs. */
#define LOAD_TICK_MS 20
static struct {
    unsigned char *map;
    unsigned len, stride, *row0, *under, *plate, *frame;
    unsigned long thread;
    volatile int stop, drawn;
} fbw __attribute__((section(".scratch")));
/* The framebuffer's pixel of LOAD_AREA's (x, y), from its top left. */
static unsigned *fb_px(int x, int y) {
    return fbw.row0 + (LOAD_AREA_X + x) * fbw.stride + (319 - (LOAD_SPIN_Y + y));
}
/* px's colours over LOAD_AREA, each pixel's alpha byte the framebuffer's own. */
static void fb_put(const unsigned *px) {
    for (int y = 0; y < LOAD_AREA_H; ++y)
        for (int x = 0; x < LOAD_AREA_W; ++x) {
            unsigned i = y * LOAD_AREA_W + x;
            *fb_px(x, y) = (px[i] & 0xffffffu) | (fbw.under[i] & 0xff000000u);
        }
}
/* A coverage 0-255 from a squared distance (half pixels) inside r2 - w and out by r2. */
static int cover(int d2, int r2, int w) {
    return d2 >= r2 ? 0 : d2 <= r2 - w ? 255 : (r2 - d2) * 255 / w;
}
/* Frame f's dots over the plate, as load_draw places and lights them, into frame. */
static void fb_dots(int f) {
    static const signed char dots[8][2] = { { 0, -1 }, { 1, -1 }, { 1, 0 },  { 1, 1 },
                                            { 0, 1 },  { -1, 1 }, { -1, 0 }, { -1, -1 } };
    static const unsigned char lit[8] = { 255, 200, 160, 128, 100, 80, 64, 56 };
    memcpy(fbw.frame, fbw.plate, LOAD_AREA_W * LOAD_AREA_H * 4);
    int cx = LOAD_SPIN_X + LOAD_SPIN_BOX / 2 - LOAD_AREA_X, cy = LOAD_SPIN_BOX / 2;
    for (int k = 0; k < 8; ++k) {
        int out = dots[k][0] && dots[k][1] ? 11 : LOAD_DOT_OUT, g = lit[(f - k) & 7];
        int dx = cx + dots[k][0] * out, dy = cy + dots[k][1] * out;
        for (int y = dy - LOAD_DOT / 2 - 1; y <= dy + LOAD_DOT / 2 + 1; ++y)
            for (int x = dx - LOAD_DOT / 2 - 1; x <= dx + LOAD_DOT / 2 + 1; ++x) {
                int u = 2 * (x - dx), v = 2 * (y - dy), a = cover(u * u + v * v, 49, 24);
                unsigned *p = &fbw.frame[y * LOAD_AREA_W + x], out_px = *p & 0xff000000u;
                for (int c = 0; c < 24; c += 8) {
                    int was = (int)(*p >> c & 255);
                    out_px |= (unsigned)(was + (g - was) * a / 255) << c;
                }
                *p = out_px;
            }
    }
}
static void *fb_painter(void *unused) {
    for (unsigned ms = 0; !fbw.stop;) {
        usleep(LOAD_TICK_MS * 1000);
        if ((ms += LOAD_TICK_MS) < LOAD_SHOW_MS || fbw.stop) continue;
        fb_dots((int)(ms / SPIN_FRAME_MS));
        fb_put(fbw.frame);
        fbw.drawn = 1;
    }
    return unused;
}
static void fb_free(void) {
    if (fbw.map) munmap(fbw.map, fbw.len);
    free(fbw.under), free(fbw.plate), free(fbw.frame);
    fbw.map = 0, fbw.under = fbw.plate = fbw.frame = 0;
}
/* The overlay with caption readied and its painter started; 0 when it cannot be (no framebuffer,
 * memory or thread, or a wait already on), the call then running with no overlay. */
int load_sync_begin(const char *caption) {
    unsigned var[40], fix[17], n = LOAD_AREA_W * LOAD_AREA_H;
    if (fbw.map) return 0;
    int fd = open("/dev/fb0", 2 /* O_RDWR */);
    if (fd < 0) return 0;
    int ok = !ioctl(fd, 0x4600 /* FBIOGET_VSCREENINFO */, var) && !ioctl(fd, 0x4602, fix) &&
             var[0] == 320 && var[1] == 375 && var[6] == 32 && fix[11] >= 1280 &&
             (fbw.under = calloc(n, 4)) && (fbw.plate = calloc(n, 4)) &&
             (fbw.frame = calloc(n, 4)) && (fbw.map = mmap(0, fix[5], 3, 1, fd, 0)) != (void *)-1 &&
             fbw.map;
    close(fd);
    if (fbw.map == (void *)-1) fbw.map = 0; /* mmap's failure */
    if (!ok) return fb_free(), 0;
    fbw.len = fix[5], fbw.stride = fix[11] / 4;
    fbw.row0 = (unsigned *)fbw.map + var[5] * fbw.stride;
    for (int y = 0; y < LOAD_AREA_H; ++y)
        for (int x = 0; x < LOAD_AREA_W; ++x) fbw.under[y * LOAD_AREA_W + x] = *fb_px(x, y);
    void *c = canvas_offline_create(LOAD_AREA_W, LOAD_AREA_H, LCD_BGRA8888),
         *lcd = c ? P(c, CANVAS_LCD) : 0;
    unsigned *px = lcd ? P(lcd, LCD_OFFLINE_FB) : 0;
    if (px && I(lcd, LCD_LINE_LENGTH) == LOAD_AREA_W * 4) {
        canvas_offline_begin_draw(c);
        memcpy(px, fbw.under, n * 4);
        load_draw(c, LOAD_AREA_X, LOAD_SPIN_Y, -1, caption);
        canvas_offline_end_draw(c);
        memcpy(fbw.plate, px, n * 4);
    }
    if (c) canvas_offline_destroy(c);
    fbw.stop = fbw.drawn = 0;
    unsigned long t = 0;
    if (!px || pthread_create(&t, 0, fb_painter, 0) || !t) return fb_free(), 0;
    fbw.thread = t;
    return 1;
}
/* The call is done: the painter joined and, if it drew, what the overlay covered put back. */
void load_sync_end(void) {
    if (!fbw.map) return;
    fbw.stop = 1;
    pthread_join(fbw.thread, 0);
    if (fbw.drawn) fb_put(fbw.under);
    fb_free();
}
/* The UI thread waiting for *done, set by work on another thread, under the same overlay. */
void load_block(volatile int *done, const char *caption) {
    int shown = load_sync_begin(caption);
    while (!*done) usleep(LOAD_TICK_MS * 1000);
    if (shown) load_sync_end();
}

/* ... or, on the selected row while Cover Flow opens, the spinner (coverflow.c coverflow_spinner)
 * in its place, white. */
void spinner_icon(void *w, void *canvas, int right, int y) {
    unsigned bitmap[64]; /* bitmap_t */
    char name[8];
    rect_t r = { right - CHEV_SHIFT - SPIN_BOX / 2, y - SPIN_BOX / 2, SPIN_BOX, SPIN_BOX };
    tk_snprintf(name, sizeof name, "spin_%d", spinner_frame());
    if (!widget_load_image(w, theme_art(name), bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
    spinner_painted(w, &r);
}

/* ... and a disabled row's (stock's list_intodown, docs/oled-design.md Chevrons and disabled rows),
 * in the half's Text Tertiary, selected or not: the wheel never selects such a row. */
void chevron_muted(void *w, void *canvas, int right, int y) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(ipod_light() ? "chevron_tl" : "chevron_td"), bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
}

/* A scrolling text's ends (list.marquee.fade, docs/oled-design.md#motion): an hscroll_label whose
 * text is wider than its column fades the text itself over MARQUEE_FADE_PX columns at the right
 * end while the text goes on past it, and at the left while it is scrolled off its start, from
 * whole at the inside to gone at the end, onto whatever is behind it (the selection's bar, its red
 * on a destructive answer, the page, art). Before stock paints the label (marquee_fade_under, from
 * its background's paint) the end columns are copied out of the canvas's buffer, which holds what
 * is behind the text; after it (marquee_fade) each column is blended back toward that copy by how
 * near the end it stands: out = behind + (painted - behind) * i / MARQUEE_FADE_PX, which is the
 * text's own coverage times the ramp over the real background. A label at rest on stock's ellipsis
 * (stopped, ellipses set) keeps it. The column is the one stock clips the text to: inside the
 * label's margin_left and margin_right (2 px unless its style says), and only where the frame's
 * clip lets it paint; a canvas whose buffer is not 32-bit draws the text clipped, unfaded. The
 * same fade serves text a painter draws itself into a column too narrow for it (Control Center's
 * labels, control_center.c cc_label): text_fade_under before it, text_fade after. */
#define MARQUEE_FADE_PX 12
#define MARQUEE_FADE_ROWS 64 /* a label's rows the copy holds; a taller label fades its first */
static struct {
    void *label;    /* the label whose ends are copied, or &mf for text_fade's */
    int x[2], y, h; /* each end's first column (-1 none) and the rows, in the buffer */
    int n;          /* the columns each end fades over, MARQUEE_FADE_PX at most */
    /* the ends' pixels (6 KB): on the heap from the first fade on, kept, as it is used by every
     * paint of a scrolling label */
    unsigned (*px)[MARQUEE_FADE_ROWS][MARQUEE_FADE_PX];
} mf __attribute__((section(".scratch")));

/* The canvas's 32-bit buffer and its pitch in pixels, else 0. */
static unsigned *fade_fb(void *canvas, int *pitch, int *fw, int *fh) {
    void *lcd = P(canvas, CANVAS_LCD);
    unsigned *fb = lcd ? P(lcd, LCD_OFFLINE_FB) : (unsigned *)0;
    if (!fb || I(lcd, LCD_FORMAT) != LCD_BGRA8888) return (unsigned *)0;
    *pitch = I(lcd, LCD_LINE_LENGTH) / 4, *fw = I(lcd, LCD_W), *fh = I(lcd, LCD_H);
    return *pitch >= *fw ? fb : (unsigned *)0;
}

/* Which ends of w fade this frame, its column's ends in *x0, *x1 (w's coordinates); 0 none. */
static int fade_ends(void *w, int *x0, int *x1) {
    if (tk_strcmp(widget_get_type(w), "hscroll_label")) return 0;
    void *style = P(w, W_ASTYLE);
    *x0 = style_get_int(style, "margin_left", 2),
    *x1 = I(w, W_W) - style_get_int(style, "margin_right", 2);
    int off = I(w, HSCROLL_XOFFSET), over = I(w, HSCROLL_TEXT_W) - (*x1 - *x0);
    if (over <= 0 || (B(w, HSCROLL_ELLIPSES) && !I(w, HSCROLL_TIMER))) return 0;
    return (off > 0) | (off < over) << 1;
}

/* Before text is drawn in the columns x0 to x1 (the canvas's coordinates), rows y to y + h: what
 * stands behind its fading ends (ends: 1 the left, 2 the right, 3 both), out of the buffer; 1 when
 * text_fade has something to blend. Each end fades over px columns (at most MARQUEE_FADE_PX; the
 * status row's strip fades over 6), which the column must hold at a fading end. */
int text_fade_under(void *canvas, int x0, int x1, int y, int h, int ends) {
    return text_fade_under_n(canvas, x0, x1, y, h, ends, MARQUEE_FADE_PX);
}
int text_fade_under_n(void *canvas, int x0, int x1, int y, int h, int ends, int px) {
    int pitch, fw, fh, clip[4];
    mf.label = (void *)0;
    unsigned *fb = canvas && ends ? fade_fb(canvas, &pitch, &fw, &fh) : (unsigned *)0;
    if (!fb || (!mf.px && !(mf.px = calloc(2, sizeof *mf.px)))) return 0; /* unfaded: clipped */
    canvas_get_clip_rect(canvas, clip);
    int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y);
    if (h > MARQUEE_FADE_ROWS) h = MARQUEE_FADE_ROWS;
    mf.n = px < 1 ? 1 : px > MARQUEE_FADE_PX ? MARQUEE_FADE_PX : px;
    mf.y = oy + y, mf.h = h;
    for (int e = 0; e < 2; ++e) {
        int x = ox + (e ? x1 - mf.n : x0);
        mf.x[e] = ends >> e & 1 && x >= clip[0] && x + mf.n <= clip[0] + clip[2] && x >= 0 &&
                          x + mf.n <= fw
                      ? x
                      : -1;
    }
    for (int r = 0; r < h; ++r) {
        int yy = mf.y + r;
        if (yy < 0 || yy >= fh || yy < clip[1] || yy >= clip[1] + clip[3]) continue;
        for (int e = 0; e < 2; ++e)
            if (mf.x[e] >= 0)
                memcpy(mf.px[e][r], fb + yy * pitch + mf.x[e], (unsigned)mf.n * sizeof **mf.px[e]);
    }
    mf.label = &mf;
    return 1;
}

/* text_fade_under's ends blended back toward what was behind them. */
static void fade_blend(void *canvas) {
    int pitch, fw, fh, clip[4];
    unsigned *fb = fade_fb(canvas, &pitch, &fw, &fh);
    if (!fb) return;
    canvas_get_clip_rect(canvas, clip);
    for (int r = 0; r < mf.h; ++r) {
        int y = mf.y + r;
        if (y < 0 || y >= fh || y < clip[1] || y >= clip[1] + clip[3]) continue;
        for (int e = 0; e < 2; ++e) {
            if (mf.x[e] < 0) continue;
            unsigned *row = fb + y * pitch + mf.x[e];
            for (int i = 0; i < mf.n; ++i) {
                /* the text's weight: 0 at the end column, nearly whole at the inside */
                unsigned k = (unsigned)(e ? mf.n - 1 - i : i), was = mf.px[e][r][i], now = row[i],
                         out = 0;
                for (int c = 0; c < 32; c += 8) {
                    int b = (int)(was >> c & 255), t = (int)(now >> c & 255);
                    out |= (unsigned)(b + (t - b) * (int)k / mf.n) << c;
                }
                row[i] = out;
            }
        }
    }
}

/* After the text text_fade_under was called for: its ends faded. */
void text_fade(void *canvas) {
    if (!canvas || mf.label != &mf) return;
    mf.label = (void *)0;
    fade_blend(canvas);
}

/* Before stock paints w's text: what stands behind its fading ends, out of the buffer. */
void marquee_fade_under(void *w, void *canvas) {
    int x0, x1, ends;
    mf.label = (void *)0;
    if (!w || !canvas || !theme()->marquee_fade || !(ends = fade_ends(w, &x0, &x1))) return;
    if (text_fade_under(canvas, x0, x1, 0, I(w, W_H), ends)) mf.label = w;
}

/* After stock painted w (ringnav_paint): its ends blended back toward what was behind them. */
void marquee_fade(void *w, void *canvas) {
    if (w && np_marq_owns(w)) np_marq_kick(w);
    if (!w || !canvas || w != mf.label) return;
    mf.label = (void *)0;
    fade_blend(canvas);
}

/* The indicator's column on the top window, repainted as it fades. */
static int sb_due(const void *info) {
    (void)info;
    sb_timer = 0;
    unsigned gone = time_now_ms() - sb_at;
    const theme_t *T = theme();
    void *top = window_manager_get_top_window(window_manager());
    if (top) {
        rect_t r = { 375 - T->sb_right - T->sb_w - 1, 0, T->sb_w + 2, I(top, W_H) };
        widget_invalidate_force(top, &r);
    }
    if (gone < UI_SHOW_MS) sb_timer = timer_add(sb_due, (void *)0, UI_FADE_STEP_MS);
    return 0;
}

/* A wheel tick moved list: the indicator shows on it, and its fade starts over. */
void wheel_sb_arm(void *list) {
    sb_at = time_now_ms();
    sb_list = list;
    sb_owner = P(list, W_PARENT); /* read now: only compared later, never followed */
    rearm(&sb_timer, sb_due, UI_SHOW_MS - UI_FADE_MS);
}

/* The indicator's alpha on list now: 255 while the wheel turns, falling to 0 over the fade. */
unsigned wheel_sb_alpha(void *list) {
    /* the wheel moves a list's scroll view; its bar is the list view's */
    /* the A-Z index takes the edge while it shows (az_index.c) */
    if ((list != sb_list && list != sb_owner) || az_on(list)) return 0;
    return ui_fade_alpha(sb_at);
}

/* A rounded rectangle's path on vg. */
void round_path(void *vg, float x, float y, float w, float h, float r) {
    vgcanvas_begin_path(vg);
    vgcanvas_move_to(vg, x + r, y);
    vgcanvas_line_to(vg, x + w - r, y);
    vgcanvas_arc(vg, x + w - r, y + r, r, -1.5707963f, 0, 0);
    vgcanvas_line_to(vg, x + w, y + h - r);
    vgcanvas_arc(vg, x + w - r, y + h - r, r, 0, 1.5707963f, 0);
    vgcanvas_line_to(vg, x + r, y + h);
    vgcanvas_arc(vg, x + r, y + h - r, r, 1.5707963f, 3.1415927f, 0);
    vgcanvas_line_to(vg, x, y + r);
    vgcanvas_arc(vg, x + r, y + r, r, 3.1415927f, 4.7123890f, 0);
    vgcanvas_close_path(vg);
}

/* BATT_SLIM: the body bw x bh at x, y, BATT_SLIM_R corners and a 1 px outline in BATT_SLIM_DIM,
 * the charge a solid fill BATT_SLIM_IN px inside it with BATT_SLIM_FILL_R corners to the level, in
 * Text Primary (red under BATT_LOW_PCT; green on a cable that charges it or holds it full, or
 * at Charge Limit; orange on one too weak), and the nub, BATT_SLIM_NUB_W x BATT_SLIM_NUB_H, a pixel
 * past the body in BATT_SLIM_DIM. Charging, Phosphor's lightning (fill, 7 px, Text Primary) sits on
 * the body's centre with a 1 px Canvas gap cut round it where it crosses the fill; full or held at
 * Charge Limit on the cable, an upright plug drawn on whole pixels (5 x 7) in its place; too weak,
 * its warning there (docs/oled-design.md#status-row, tools/art.py BATT_GLYPHS: the gap is
 * the image's own, BATT_GLYPH_BOX px with the glyph's centre BATT_GLYPH_X, BATT_GLYPH_Y in from its
 * corner). Final colours; the caller restores the fill. */
#define BATT_SLIM_R 4.0f
#define BATT_SLIM_IN 2.0f
#define BATT_SLIM_FILL_R 2.2f
#define BATT_SLIM_NUB_W 1.5f
#define BATT_SLIM_NUB_H 4.4f
#define BATT_SLIM_NUB_R 0.8f
#define BATT_SLIM_DIM RGB_TERTIARY_L
#define BATT_GLYPH_X 4.5f /* tools/art.py BATT_GLYPH_BOX (9 x 10) and the glyph's centre in it */
#define BATT_GLYPH_Y 5.0f
/* The charge fill's share of the body, in percent, for battery_now's key: the level, on the cable
 * as off it. */
static unsigned battery_fill(unsigned key) { return BATT_KEY_LEVEL(key); }
/* The fill's colour for key, ink being Text Primary's. */
static unsigned battery_tone(unsigned key, unsigned ink) {
    return key & BATT_KEY_WEAK                            ? BATT_WEAK_RGB
           : key & (BATT_KEY_CHARGING | BATT_KEY_PLUGGED) ? BATT_CHARGE_RGB
           : BATT_KEY_LEVEL(key) < BATT_LOW_PCT           ? BATT_LOW_RGB
                                                          : ink;
}
/* The glyph inside the body for key, a theme image's name, or 0 for none. */
static const char *battery_glyph(unsigned key) {
    int light = ipod_light();
    if (key & BATT_KEY_WEAK) return theme_art(light ? "battwarn_l" : "battwarn_d");
    if (key & BATT_KEY_CHARGING) return theme_art(light ? "battbolt_l" : "battbolt_d");
    if (key & BATT_KEY_PLUGGED) return theme_art(light ? "battplug_l" : "battplug_d");
    return (const char *)0;
}
void battery_slim(void *w, void *canvas, int x, int y, int bw, int bh, unsigned key) {
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    unsigned pct = battery_fill(key), level = battery_tone(key, tok(TOK_PRIMARY));
    float fx = (float)x + BATT_SLIM_IN, fy = (float)y + BATT_SLIM_IN,
          fw = ((float)bw - 2 * BATT_SLIM_IN) * (float)pct / 100, fh = (float)bh - 2 * BATT_SLIM_IN;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final: Light Mode's flip leaves them */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    round_path(vg, (float)x + 0.5f, (float)y + 0.5f, (float)bw - 1, (float)bh - 1, BATT_SLIM_R);
    vgcanvas_set_line_width(vg, 1);
    vgcanvas_set_stroke_color(vg, RGBA(BATT_SLIM_DIM));
    vgcanvas_stroke(vg);
    if (fw > 0) {
        round_path(vg, fx, fy, fw, fh, fw < 2 * BATT_SLIM_FILL_R ? fw / 2 : BATT_SLIM_FILL_R);
        vgcanvas_set_fill_color(vg, RGBA(level));
        vgcanvas_fill(vg);
    }
    round_path(vg, (float)(x + bw + 1), (float)y + ((float)bh - BATT_SLIM_NUB_H) / 2,
               BATT_SLIM_NUB_W, BATT_SLIM_NUB_H, BATT_SLIM_NUB_R);
    vgcanvas_set_fill_color(vg, RGBA(BATT_SLIM_DIM));
    vgcanvas_fill(vg);
    vgcanvas_restore(vg);
    st.raw_ink = raw;
    const char *glyph = battery_glyph(key);
    if (glyph && w) /* whole pixels: the image holds the glyph's half-pixel place */
        paint_art(w, canvas, glyph, x + (int)((float)bw / 2 - BATT_GLYPH_X + 0.5f),
                  y + (int)((float)bh / 2 - BATT_GLYPH_Y + 0.5f));
}

/* settings.values.switch: an on/off setting's switch (docs/oled-design.md List row), SWITCH_W x
 * SWITCH_H, fully round, ending on screen column right (canvas columns) and centred on row y, its
 * SWITCH_KNOB px knob SWITCH_KNOB_IN px in. Off the selection: the accent's track when on, Track
 * (each half's) when off, a white knob. On the selection, whose bar is the accent: a white track
 * with an accent knob when on, a SWITCH_RING px white outline round a white knob when off. Final
 * colours. Every switch is drawn here, stock's image switches too (paint_into), so all of them move
 * alike: a switch seen at the same place before with the other value slides its knob there over
 * SWITCH_MS, easing out, and the track's colour crosses with it (unselected) or turns halfway
 * (selected); under Reduce Motion it is there at once. A change during the slide starts from where
 * the knob is. A switch first seen at a place (a page opening, a list scrolled) stands still. The
 * slide's frames are ui_kit.c's shared timer's. */
#define SWITCH_W 40.0f
#define SWITCH_H 24.0f
#define SWITCH_KNOB 20.0f
#define SWITCH_KNOB_IN 2.0f
#define SWITCH_RING 1.5f
#define SWITCH_MS 200
#define SWITCH_SLOTS 4 /* the switches remembered: a page's in sight, the last few painted */
static struct {
    struct {
        int x, y, on;
        anim_t slide; /* the knob's run (ui_kit.h): MO_SPATIAL, at once under Reduce Motion */
        short from;   /* the knob's place at its start, 0 off .. 1000 on */
    } s[SWITCH_SLOTS];
    unsigned next;
} sw __attribute__((section(".scratch")));

/* the knob's place now in slot k, 0 off .. 1000 on */
static int sw_place(int k) {
    int to = sw.s[k].on ? 1000 : 0;
    return sw.s[k].from + (to - sw.s[k].from) * anim_at(&sw.s[k].slide) / 1000;
}
/* the knob's place for a switch at screen x, y showing on: 0 off .. 1000 on */
static int sw_knob(int x, int y, int on) {
    int k = 0;
    while (k < SWITCH_SLOTS && (sw.s[k].x != x || sw.s[k].y != y || !sw.s[k].slide.at)) ++k;
    if (k == SWITCH_SLOTS) { /* first seen here: at rest */
        k = (int)(sw.next++ % SWITCH_SLOTS);
        sw.s[k].x = x, sw.s[k].y = y, sw.s[k].on = on, sw.s[k].from = (short)(on ? 1000 : 0);
        anim_start(&sw.s[k].slide, 0, EASE_CUBIC_OUT, MO_SPATIAL);
    } else if (sw.s[k].on != on) { /* flipped: slide from where it is */
        sw.s[k].from = (short)sw_place(k);
        sw.s[k].on = on;
        anim_start(&sw.s[k].slide, SWITCH_MS, EASE_CUBIC_OUT, MO_SPATIAL);
    }
    return sw_place(k);
}
static unsigned sw_mix(unsigned a, unsigned b, int t) { /* a to b by t thousandths */
    unsigned out = 0;
    for (int sh = 0; sh < 24; sh += 8) {
        int ca = (int)(a >> sh & 255), cb = (int)(b >> sh & 255);
        out |= (unsigned)(ca + (cb - ca) * t / 1000) << sh;
    }
    return out;
}

void switch_mark(void *canvas, int right, int y, int on, int selected) {
    void *vg = canvas ? canvas_get_vgcanvas(canvas) : (void *)0;
    if (!vg) return;
    int p = sw_knob(right + I(canvas, CANVAS_X), y + I(canvas, CANVAS_Y), on), lit = p >= 500;
    unsigned accent = scheme()->tones[0] & 0xffffffu,
             track =
                 selected ? 0xffffff : sw_mix(ipod_light() ? RGB_TRACK_L : RGB_TRACK, accent, p),
             knob = selected && lit ? accent : 0xffffff;
    float x = (float)right - SWITCH_W, top = (float)y - SWITCH_H / 2,
          kx = x + SWITCH_KNOB_IN + (SWITCH_W - 2 * SWITCH_KNOB_IN - SWITCH_KNOB) * (float)p / 1000;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final: Light Mode's flip leaves them */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    if (selected && !lit) { /* the outline's centre line SWITCH_RING / 2 inside the track's edge */
        round_path(vg, x + SWITCH_RING / 2, top + SWITCH_RING / 2, SWITCH_W - SWITCH_RING,
                   SWITCH_H - SWITCH_RING, (SWITCH_H - SWITCH_RING) / 2);
        vgcanvas_set_line_width(vg, SWITCH_RING);
        vgcanvas_set_stroke_color(vg, RGBA(track));
        vgcanvas_stroke(vg);
    } else {
        round_path(vg, x, top, SWITCH_W, SWITCH_H, SWITCH_H / 2);
        vgcanvas_set_fill_color(vg, RGBA(track));
        vgcanvas_fill(vg);
    }
    round_path(vg, kx, top + SWITCH_KNOB_IN, SWITCH_KNOB, SWITCH_KNOB, SWITCH_KNOB / 2);
    vgcanvas_set_fill_color(vg, RGBA(knob));
    vgcanvas_fill(vg);
    vgcanvas_restore(vg);
    st.raw_ink = raw;
}

/* A grip (Edit Library's and Edit Home's drag handle, docs/oled-design.md#edit-library): three
 * GRIP_W x GRIP_H bars GRIP_GAP apart, its middle at cx, cy, in rgb (final), scaled by pct. */
#define GRIP_W 18
#define GRIP_H 2
#define GRIP_GAP 5
void grip_mark(void *canvas, int cx, int cy, unsigned rgb, int pct) {
    int w = GRIP_W * pct / 100, gap = GRIP_GAP * pct / 100;
    unsigned raw = ui_ink_raw(1);
    stock_canvas_set_fill_color(canvas, RGBA(rgb));
    for (int k = -1; k <= 1; ++k) canvas_fill_rect(canvas, cx - w / 2, cy + k * gap - 1, w, GRIP_H);
    ui_ink_raw(raw);
}

/* A lifted row's raised card (docs/oled-design.md#edit-library): card, CARD_RGB with LIFT_R
 * corners, a 1 px CARD_RING ring and a soft black shadow 6 px down, each half's. */
#define CARD_RGB RGB_RAISED_2
#define CARD_RING 0x141414
#define LIFT_R 9
void lift_card_paint(void *canvas, const rect_t *card) {
    int light = ipod_light();
    unsigned raw = ui_ink_raw(1);
    rect_t ring = { card->x - 1, card->y - 1, card->w + 2, card->h + 2 }, c = *card;
    for (int k = 3; k > 0; --k) { /* its shadow, softening out */
        rect_t s = { card->x - 2 * k, card->y + 6 - 2 * k, card->w + 4 * k, card->h + 4 * k };
        fill_box(canvas, &s, (RGBA(0) & 0xffffffu) | (unsigned)(0x30 - 0x0c * k) << 24,
                 (unsigned)(LIFT_R + 2 * k));
    }
    fill_box(canvas, &ring, RGBA(flip_if(light, CARD_RING)), LIFT_R + 1);
    fill_box(canvas, &c, RGBA(flip_if(light, CARD_RGB)), LIFT_R);
    ui_ink_raw(raw);
}

/* A row's check circle (Edit Library's and Edit Home's show or hide,
 * docs/oled-design.md#edit-library), CIRCLE_D px across, its middle at canvas cx, cy: CIRCLE_ON
 * the accent's disc with a white check (tools/art.py circle_check), CIRCLE_OFF a 1.5 px ring in
 * Text Tertiary, CIRCLE_LOCKED a gray disc with a padlock in Text Secondary (circle_lock). On the
 * selection (selected) the disc is white and its check the accent's, the ring white. */
#define CIRCLE_D 16.0f
#define CIRCLE_RING 1.5f
#define CIRCLE_LOCK_RGB RGB_OFF
#define CIRCLE_CHECK_PX 11 /* the art's glyphs' boxes */
#define CIRCLE_LOCK_PX 10
void check_circle(void *w, void *canvas, int cx, int cy, int state, int selected) {
    void *vg = canvas ? canvas_get_vgcanvas(canvas) : (void *)0;
    if (!vg) return;
    int light = ipod_light();
    unsigned accent = scheme()->tones[0] & 0xffffffu,
             fill = state == CIRCLE_LOCKED ? flip_if(light, CIRCLE_LOCK_RGB)
                    : selected             ? 0xffffff
                    : state == CIRCLE_ON   ? accent
                                           : tok(TOK_TERTIARY);
    float x = (float)cx - CIRCLE_D / 2, y = (float)cy - CIRCLE_D / 2;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final: Light Mode's flip leaves them */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    if (state == CIRCLE_OFF) { /* the ring's centre line CIRCLE_RING / 2 inside the disc's edge */
        float in = CIRCLE_RING / 2;
        round_path(vg, x + in, y + in, CIRCLE_D - CIRCLE_RING, CIRCLE_D - CIRCLE_RING,
                   (CIRCLE_D - CIRCLE_RING) / 2);
        vgcanvas_set_line_width(vg, CIRCLE_RING);
        vgcanvas_set_stroke_color(vg, RGBA(fill));
        vgcanvas_stroke(vg);
    } else {
        round_path(vg, x, y, CIRCLE_D, CIRCLE_D, CIRCLE_D / 2);
        vgcanvas_set_fill_color(vg, RGBA(fill));
        vgcanvas_fill(vg);
    }
    vgcanvas_restore(vg);
    st.raw_ink = raw;
    if (state == CIRCLE_LOCKED)
        paint_art(w, canvas, theme_art(light ? "circle_lock_l" : "circle_lock_d"),
                  cx - CIRCLE_LOCK_PX / 2, cy - CIRCLE_LOCK_PX / 2);
    else if (state == CIRCLE_ON)
        paint_art(w, canvas, theme_art(selected ? "circle_check_a" : "circle_check_w"),
                  cx - CIRCLE_CHECK_PX / 2, cy - CIRCLE_CHECK_PX / 2);
}

/* The selection's bar without its lean (docs/oled-design.md#selection-bar), for pages that paint
 * their own rows: the accent from the theme's sel_inset in from x and from the right of x + w,
 * its ends sel_radius, over the row's y and h (canvas coordinates). The fill is put back. */
void sel_bar_flat(void *canvas, int x, int y, int w, int h) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd) return;
    const theme_t *T = theme();
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    rect_t bar = { x + T->sel_inset, y, w - 2 * T->sel_inset, h };
    fill_box(canvas, &bar, RGBA(scheme()->tones[0]), (unsigned)T->sel_radius);
    stock_canvas_set_fill_color(canvas, fill);
}
