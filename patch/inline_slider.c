/* Inline slider rows (docs/oled-design.md#inline-sliders; the user's, 2026-11-16, from
 * design/mockups/mock_inline_slider.py): a setting with one value changes in its list, not on a
 * page of its own. At rest the row holds its title, a short track (IS_TRACK_X to IS_VALUE_W short
 * of the right column) and its value on the right column. Centre or a tap grabs it: the row grows
 * to IS_GROWN (IS_GROWN_ENDS with Balance's Left and Right under the track) over IS_MS, as a
 * captioned row grows (settings_pages.c), its value in Inter Medium on the right column and a
 * full-width track under the title; the wheel sets the value live, at the wheel's speed (levels.c
 * wheel_level), and Centre or Return sets it down. Each value is set through stock's own setter, as
 * stock's page for it sets it on each step, so nothing waits for the page: Max Volume g_maxvolume,
 * the volume pulled down with it (stock's 0x4bb360); Balance config_balance (the DAC's ioctl and
 * PLAYSET BALANCE); Brightness config_lightness (the backlight and SYSSET LIGHTNESS); Default
 * Volume's level g_bootvolume (stock's 0x4b73b8). A setting with a switch and a value (Default
 * Volume) is a switch row with its slider row under it, shown only while the switch is on
 * (inline_pair). The rows are drawn from their
 * list_item's paint (inline_slider_paint, row_paint.c ringnav_paint), grown on a timer (is_step),
 * set down when the list's selection paints off a grabbed row (inline_slider_sel, row_paint.c
 * paint_selection), and take the keys while grabbed (inline_slider_key, nav/input.c ringnav). One
 * list's rows at a time, IS_ROWS of them: a row from another list clears the table. */
#include "navigation.h"

#define IS_ROWS 8
#define IS_PITCH 32      /* a row at rest, title and track */
#define IS_GROWN 64      /* a grabbed row */
#define IS_GROWN_ENDS 82 /* ... with the range's ends under its track */
#define IS_MS 150        /* the grow and the set-down, as a caption's (CAP_MS) */
#define IS_TRACK_X 190   /* the resting track's left, on the screen */
#define IS_VALUE_W 62    /* its right end this far short of the right column: the value's room */
#define IS_TRACK_H 4
#define IS_KNOB 12         /* the resting knob */
#define IS_GROWN_KNOB 16   /* a grabbed row's */
#define IS_GROWN_INSET 10  /* a grabbed row's track, in from the text and right columns */
#define IS_TRACK_Y 43      /* its centre, from the row's top */
#define IS_ENDS_Y 62       /* its ends' line */
#define IS_TICK_H 12       /* Balance's centre mark */
#define IS_ON_BAR 0x7da2ea /* a track on the accent's bar (the mock's) */
#define IS_VALUE_PX TYPE(15)
#define IS_GRABBED_PX TYPE(20) /* Inter Medium */
#define IS_ENDS_PX TYPE(13)

typedef struct {
    const char *title;
    signed char min, max;
    unsigned char centre; /* the fill runs from the middle, where neither side is turned down */
    const char *lo, *hi;  /* the ends under a grabbed track; 0: none */
} is_kind_t;
static const is_kind_t kinds[IS_KINDS] = {
    [IS_MAXVOL] = { "Max Volume", 0, 100, 0, 0, 0 },
    [IS_BALANCE] = { "Balance", -10, 10, 1, "Left", "Right" },
    [IS_BRIGHT] = { "Brightness", 0, 100, 0, 0, 0 },
    [IS_BOOTVOL] = { "Level", 0, 100, 0, 0, 0 },
};

static struct {
    void *view, *item[IS_ROWS], *sw[IS_ROWS]; /* each row's list_item; a pair's switch label */
    unsigned char kind[IS_ROWS];
    void *open, *closing; /* the row grabbed (growing or grown) and the one set down, shrinking */
    anim_t run;           /* their change (ui_kit.h): MO_SPATIAL, at once under Reduce Motion */
    unsigned timer;       /* is_step's, while it runs */
} is __attribute__((section(".scratch")));

static int is_gone(void *view, void *event) {
    (void)event;
    if (view != is.view) return 0;
    stop_timer(&is.timer);
    memset(&is, 0, sizeof is);
    return 0;
}
/* A free slot in view's table, which a row from another view clears; -1 when full. */
static int slot_new(void *view) {
    if (view != is.view) {
        memset(&is, 0, sizeof is);
        is.view = view;
        widget_on(view, EVT_DESTROY, is_gone, view);
    }
    for (int i = 0; i < IS_ROWS; ++i)
        if (!is.item[i]) return i;
    return -1;
}
static int slot_of(const void *item) {
    for (int i = 0; item && i < IS_ROWS; ++i)
        if (is.item[i] == item) return i;
    return -1;
}

int inline_value(int k) {
    if (k == IS_MAXVOL) return g_maxvolume;
    if (k == IS_BALANCE) return mclGetBalance();
    if (k == IS_BRIGHT) return g_lightness;
    return g_bootvolume;
}

/* k's value to v (within its range: wheel_level's), set and saved as stock's page for it does a
 * step. */
static void set_value(int k, int v) {
    if (k == IS_MAXVOL) {
        g_maxvolume = (unsigned char)v;
        if (v < g_volume) { /* the volume never above its top */
            g_volume = (unsigned char)v;
            device_set_volume(v, 1);
            write_int_config(g_volume, "PLAYSET", "VOLUME");
        }
        write_int_config(g_maxvolume, "PLAYSET", "MAXVOLUME");
    } else if (k == IS_BALANCE)
        config_balance(v);
    else if (k == IS_BRIGHT)
        config_lightness(v);
    else {
        g_bootvolume = (unsigned char)v;
        write_int_config(v, "PLAYSET", "BOOTVOLUME");
    }
}

void inline_value_text(int k, int v, char *out, unsigned n) {
    if (k == IS_BALANCE)
        v ? tk_snprintf(out, n, "%c %d%%", v < 0 ? 'L' : 'R', (v < 0 ? -v : v) * 10)
          : tk_snprintf(out, n, "Center");
    else if (k == IS_BRIGHT)
        tk_snprintf(out, n, "%d%%", v);
    else
        tk_snprintf(out, n, "%d", v);
}

static int full_h(int k) { return kinds[k].lo ? IS_GROWN_ENDS : IS_GROWN; }

static void repaint(void *item) {
    void *win = item ? window_of(item) : (void *)0;
    if (win) widget_invalidate_force(win, (void *)0);
}

/* The rows' heights for the change's progress, the list laid out again when they moved and the
 * grabbed row's foot kept in sight on or above the list end (nav/lists.c list_shown_h), the view
 * moved by whole rows; 1 while the change runs. */
static int heights(void) {
    void *view = is.view;
    int k = anim_at(&is.run), moved = 0;
    for (int i = 0; view && i < IS_ROWS; ++i) {
        void *it = is.item[i];
        if (!it) continue;
        int full = full_h(is.kind[i]), h = it == is.open ? IS_PITCH + (full - IS_PITCH) * k / 1000
                                           : it == is.closing ? full - (full - IS_PITCH) * k / 1000
                                                              : IS_PITCH;
        if (I(it, W_H) != h) I(it, W_H) = h, moved = 1;
    }
    if (k >= 1000) is.closing = (void *)0;
    if (!moved) return k < 1000;
    void *list = P(view, W_PARENT), *o = is.open;
    if (list) widget_layout(list);
    if (o) list_grown_foot(view, o, IS_PITCH);
    widget_invalidate_force(view, (void *)0);
    return k < 1000;
}
/* Each MO_FRAME_MS while a row grows or shrinks, as a captioned row's (settings_pages.c cap_step):
 * the heights follow the change on a timer, not on the selection's paints, which can stop coming.
 */
static int is_step(const void *unused) {
    (void)unused;
    if (heights()) return 8; /* RET_REPEAT */
    is.timer = 0;
    return 7; /* RET_REMOVE */
}
/* A grab or a set-down starts the change: at once under Reduce Motion (MO_SPATIAL). */
static void change(void) {
    anim_start(&is.run, IS_MS, EASE_QUAD_OUT, MO_SPATIAL);
    heights();
    if (!is.timer) is.timer = timer_add(is_step, (void *)0, MO_FRAME_MS);
}

/* The grabbed row set down, shrinking back. */
static void set_down(void) {
    if (!is.open) return;
    is.closing = is.open, is.open = (void *)0;
    change();
    repaint(is.closing);
}

/* A row's click (Centre, or a tap): it grabs the row, or sets the grabbed one down. */
static int is_click(void *ctx, void *event) {
    (void)event;
    int i = (int)(long)ctx;
    void *item = i >= 0 && i < IS_ROWS ? is.item[i] : (void *)0;
    if (!item) return 0;
    if (item == is.open) {
        set_down();
        return 0;
    }
    if (is.closing) I(is.closing, W_H) = IS_PITCH; /* still shrinking: at rest at once */
    is.closing = is.open, is.open = item;
    st.touch_mode = 0;                          /* the bar shows on it, as after the wheel */
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* the wheel at once, as Edit Library's Centre */
    change();
    repaint(item);
    return 0;
}

/* view's row of kind k (an icon as its neighbours'), a plain row with no chevron; its label. */
static void *slider_row(void *view, const char *icon, int k, int i) {
    void *label = list_row(view, icon, is_click, (void *)(long)i);
    widget_set_text_utf8(label, kinds[k].title);
    is.item[i] = P(P(label, W_PARENT), W_PARENT);
    is.kind[i] = (unsigned char)k;
    return label;
}
void *inline_slider_row(void *view, const char *icon, int k) {
    int i = slot_new(view);
    return i < 0 ? list_row(view, icon, 0, 0) : slider_row(view, icon, k, i);
}

/* The pair's switch as its setting is, and its slider row shown only while it is on. */
static void pair_sync(int i) {
    int on = g_bootvol_flag != 0;
    list_toggle(is.sw[i], "Default Volume", on);
    widget_set_visible(is.item[i], on, 0);
}
static int pair_click(void *ctx, void *event) {
    (void)event;
    int i = (int)(long)ctx;
    if (i < 0 || i >= IS_ROWS || !is.sw[i]) return 0;
    g_bootvol_flag = !g_bootvol_flag; /* as stock's switch (0x4b7334) */
    write_int_config(g_bootvol_flag, "PLAYSET", "BOOTVOLFLAG");
    if (is.item[i] == is.open) is.open = (void *)0;
    int id = widget_get_prop_int(is.view, SEL, -1);
    pair_sync(i);
    void *list = P(is.view, W_PARENT);
    if (list) widget_layout(list);
    /* The list's row count changed, which resets a live list's selection (lists.c load): the
     * switch's row keeps it, the slider row being under it. The first select loads the new count
     * (g_menu.rows), the second stores it with the row. */
    if (id >= 0) {
        ringnav_select(is.view, id, 0);
        ringnav_select(is.view, id, g_menu.rows);
    }
    repaint(is.item[i]);
    return 0;
}
/* A switch row for k's setting, its slider row under it: two of view's rows. */
void inline_pair(void *view, const char *icon, int k) {
    int i = slot_new(view);
    if (i < 0) return;
    void *sw = list_row(view, icon, pair_click, (void *)(long)i);
    slider_row(view, icon, k, i);
    is.sw[i] = sw;
    pair_sync(i);
}

/* Each paint of the list's selection (row_paint.c paint_selection): item the selected row's
 * list_item, 0 with none showing. The selection moved off the grabbed row (a tap on another) sets
 * it down. */
void inline_slider_sel(void *view, void *item) {
    if (!is.view || view != is.view) return;
    if (is.open && item && item != is.open) set_down();
}

/* navigation.c's key filter, a key's release on top: 1 if a grabbed row took it. The wheel sets its
 * value (owned: its press was silenced, so a step clicks), Centre or Return sets it down. A grabbed
 * row whose page is no longer on top is set down and takes nothing. */
int inline_slider_key(void *top, unsigned key, int owned) {
    void *item = is.open;
    if (!item) return 0;
    if (window_of(item) != top || !usable()) {
        set_down();
        return 0;
    }
    if (key == KEY_CENTER ||
        (key == KEY_RETURN && !*(volatile unsigned char *)RETURN_RELEASE_LATCH)) {
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
        set_down();
        return 1;
    }
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0, i = slot_of(item);
    if (!dir || i < 0) return 0;
    int k = is.kind[i], v = inline_value(k),
        to = wheel_level(v, kinds[k].min, kinds[k].max, kinds[k].max - kinds[k].min, dir);
    if (to != v) {
        set_value(k, to);
        if (owned) buzzeer_switch(1);
        widget_invalidate_force(item, (void *)0);
    }
    return 1;
}

/* the track from x0 to x1 on row cy, filled to v's place, its knob d px across */
static void track(void *canvas, int x0, int x1, int cy, int k, int v, int sel, int d) {
    const is_kind_t *K = &kinds[k];
    int span = K->max - K->min, kx = x0 + (x1 - x0) * (v - K->min) / (span ? span : 1);
    unsigned fill = sel ? 0xffffff : scheme()->tones[0] & 0xffffffu;
    rect_t t = { x0, cy - IS_TRACK_H / 2, x1 - x0, IS_TRACK_H };
    fill_box(canvas, &t, RGBA(sel ? IS_ON_BAR : tok(TOK_TRACK)), IS_TRACK_H / 2);
    if (K->centre) {
        rect_t tick = { (x0 + x1) / 2 - 1, cy - IS_TICK_H / 2, 2, IS_TICK_H };
        fill_box(canvas, &tick, RGBA(sel ? IS_ON_BAR : tok(TOK_TERTIARY)), 0);
    } else if (kx > x0) {
        rect_t f = { x0, t.y, kx - x0, IS_TRACK_H };
        fill_box(canvas, &f, RGBA(fill), IS_TRACK_H / 2);
    }
    if (!sel && ipod_light()) { /* white on white: a Track edge, as the slider page's knob */
        rect_t e = { kx - d / 2 - 1, cy - d / 2 - 1, d + 2, d + 2 };
        fill_box(canvas, &e, RGBA(tok(TOK_TRACK)), (unsigned)(d / 2 + 1));
    }
    rect_t kb = { kx - d / 2, cy - d / 2, d, d };
    fill_box(canvas, &kb, RGBA(0xffffff), (unsigned)(d / 2));
}

/* After a row's list_item paints its children (row_paint.c ringnav_paint): its track and value at
 * rest, or grabbed, as far as it has grown, the value large, the full track and any ends. */
void inline_slider_paint(void *w, void *canvas) {
    int i = w && is.view && P(w, W_PARENT) == is.view ? slot_of(w) : -1;
    void *lcd = i >= 0 ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || !widget_get_visible(w)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    int k = is.kind[i], v = inline_value(k), h = I(w, W_H), ox = page_ox(canvas, w);
    int tx = geom_val(V_TEXT_X) - ox, rx = 375 - geom_val(V_RIGHT_X) - ox, cy = IS_PITCH / 2;
    int sel = w == is.open || (st.sel_accent && P(st.sel_accent, W_PARENT) == w);
    char s[16];
    inline_value_text(k, v, s, sizeof s);
    st.raw_ink = 1; /* final colours, each half's */
    if (h > IS_PITCH || w == is.open) {
        ui_text(canvas, s, 1, IS_GRABBED_PX, 0xffffff, 1, rx, cy, rx - tx, 3);
        if (IS_TRACK_Y + IS_GROWN_KNOB / 2 <= h)
            track(canvas, tx + IS_GROWN_INSET, rx - IS_GROWN_INSET, IS_TRACK_Y, k, v, 1,
                  IS_GROWN_KNOB);
        if (kinds[k].lo && IS_ENDS_Y + IS_ENDS_PX / 2 <= h) {
            ui_text(canvas, kinds[k].lo, 0, IS_ENDS_PX, RGB_CAPTION_ON_ACCENT, 1,
                    tx + IS_GROWN_INSET, IS_ENDS_Y, rx - tx, 2);
            ui_text(canvas, kinds[k].hi, 0, IS_ENDS_PX, RGB_CAPTION_ON_ACCENT, 1,
                    rx - IS_GROWN_INSET, IS_ENDS_Y, rx - tx, 3);
        }
    } else {
        ui_text(canvas, s, 0, IS_VALUE_PX, sel ? RGB_CAPTION_ON_ACCENT : tok(TOK_SECONDARY), 1, rx,
                cy, IS_VALUE_W, 3);
        track(canvas, IS_TRACK_X - ox, rx - IS_VALUE_W, cy, k, v, sel, IS_KNOB);
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}
