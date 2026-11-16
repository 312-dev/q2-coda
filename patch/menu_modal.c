/* The menu sheet (docs/oled-design.md#menus): the hold menus, Now Playing's More, a playlist's More
 * and the pickers in them, under a set with Geometry.modal (OLED's), as a frosted page sheet over
 * the page they act on (the user's C3, 2026-11-16). The page under it recedes: drawn at SH_SHRINK
 * of its size about the screen's centre and dimmed (half way to black; Light Mode SH_DIM_L), it
 * shows above the sheet, whose top is SH_TOP with SH_R corners and a grab bar. The sheet's material
 * is that page blurred, its colour raised, darkened and tinted (sheet_build): made once from the
 * first whole paint, never per frame. On it the item's header (its cover, name and a line under
 * it) and the rows on the house list grid scroll as one, the rows edge to edge: past the list end
 * they fade into the material (sheet_fade), a lit row never, and the wheel's last row ends on it.
 * A row's symbol (Phosphor, regular, tools/art.py MENU_ICONS) stands on the right column, a
 * setting's before its label with the value at the right. The list's own selection bar lights the
 * rows; a destructive row is red, its bar too (modal_red). */
#include "navigation.h"

extern unsigned char now_cover(void *r, const char **file); /* coverflow.c */
extern unsigned album_key(void *r);                         /* coverflow.c */
extern char *art_path(char *out, unsigned key, const char *suffix);

#define MODAL_ROW 32
#define MODAL_ROWS 6
#define MODAL_GAP 12             /* before a destructive row */
#define MODAL_LEAD 26            /* a setting's or a choice's label after its leading symbol */
#define MODAL_ICON 19            /* tools/art.py MENU_ICON_PX */
#define MODAL_SWITCH 34          /* list_marks.c switch_mark's width */
#define MODAL_FILL 0x1c1c1e      /* the sheet before its material is made */
#define MODAL_SECONDARY 0xababab /* Text Secondary; the light half's below */
#define MODAL_SECONDARY_L 0x5c5c5c

#define SH_W 375
#define SH_H 320
#define SH_TOP 40     /* the sheet's top on screen */
#define SH_R 40       /* its top corners */
#define SH_STRIP 80   /* the rows above SH_TOP + SH_R the receding page can show in */
#define SH_GRAB_Y 46  /* the grab bar, 36 x 5 */
#define SH_CONTENT 50 /* the header's top, and the rows' list's */
#define SH_BARE 58    /* the rows' top with no header: the page's first row */
#define SH_HEAD 72    /* the header: the cover SH_ART square at the text column, 6 px down */
#define SH_ART 52
#define SH_NAME_X 88
#define SH_FADE_TOP 14 /* a scrolled list's top fade */
#define SH_FOOT 266    /* the foot fade's start: alpha 1 here, 0 at the screen's foot */
#define SH_GRAB 0x5a5a5e
#define SH_GRAB_L 0xa1a1a6
/* the material (design/menu-overlay mock_menu_final.py), in 1/256: the receding page's scale and
 * the part of it kept, the blur's quarter-size grid and box radius (3 passes, about a 20 px
 * gaussian), the colour boost, the brightness kept and the white over it; Light Mode white at 75%
 */
#define SH_SHRINK 241 /* 0.94 */
#define SH_PAGE 128   /* the receding page kept: half way to black */
#define SH_PAGE_L 166 /* Light Mode: black at 35% */
#define SH_QW 94
#define SH_QH 80
#define SH_BLUR 5
#define SH_SAT 410 /* 1.6 */
#define SH_KEEP 72 /* 0.28 */
#define SH_TINT 15 /* 0.06 */
#define SH_TINT_L 192

/* A row's props: its symbol's art stem (tools/art.py menu_<stem>_<ink>), its value, how they
 * stand (MM_*), and whether it is destructive. */
#define MM_ICON "_mm_icon"
#define MM_VALUE "_mm_value"
#define MM_HOW "_mm_how"
#define MM_RED "_mm_red"
#define MM_HEAD "_mm_head" /* the header's space at the top of a list's rows */

/* The open modals, the top one last: a picker over More keeps More's (hidden) under it. Each has
 * its header: the item's name, the line under it and its cover's file:// URL, "" for none. */
#define MODAL_MAX 2
typedef struct {
    char title[96], sub[96], url[160];
} head_t;
static struct {
    void *win, *list, *view;
    int top, high; /* the rows' box on screen */
    head_t head;
} modals[MODAL_MAX] __attribute__((section(".scratch")));
static head_t head_next __attribute__((section(".scratch"))); /* modal_head's, for the next frame */
/* The material, shared by the open modals (they stand over one page): SH_STRIP rows of the page
 * receding, then the sheet's rows from SH_TOP; edge the corner's inset by row, in 1/16 px. */
static struct {
    unsigned *buf;
    unsigned short edge[SH_R];
} fr __attribute__((section(".scratch")));
/* The foot fade's alpha, 255 (1 - t)^1.6 at each row's middle from SH_FOOT to the screen's foot */
static const unsigned char foot_alpha[SH_H - SH_FOOT] = {
    251, 243, 236, 229, 221, 214, 207, 200, 193, 187, 180, 173, 167, 160, 154, 148, 142, 136,
    130, 124, 118, 113, 107, 102, 96,  91,  86,  81,  76,  72,  67,  62,  58,  54,  49,  45,
    42,  38,  34,  31,  27,  24,  21,  18,  15,  13,  10,  8,   6,   4,   3,   1,   0,   0
};

int modal_set(void) { return geom_val(V_MODAL); }

/* The rows span the screen; their text and symbols stand on the house list grid. */
int modal_row_w(void) { return SH_W; }
static int text_x(void) { return geom_val(V_TEXT_X); }
static int right_x(void) { return SH_W - geom_val(V_RIGHT_X); }

static int modal_gone(void *win, void *event) {
    int open = 0;
    for (int i = 0; i < MODAL_MAX; ++i) {
        if (modals[i].win == win) modals[i].win = modals[i].list = modals[i].view = (void *)0;
        open |= modals[i].win != 0;
    }
    if (!open && fr.buf) free(fr.buf), fr.buf = (unsigned *)0;
    return 0;
}

/* An open modal's window (navigation.c paint_selection keeps the row under it lit). */
int modal_win(void *w) {
    for (int i = 0; w && i < MODAL_MAX; ++i)
        if (modals[i].win == w) return 1;
    return 0;
}

/* The rows' surface (the list's scroll_view) of an open modal, painted by modal_row_paint. */
int modal_rows_of(void *w) {
    for (int i = 0; w && i < MODAL_MAX; ++i)
        if (modals[i].view == w) return 1;
    return 0;
}

/* w's top on the screen. */
static int screen_y(void *w) {
    int y = 0;
    for (void *p = w, *wm = window_manager(); p && p != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    return y;
}

/* The height a lit row scrolls in on an open modal's rows (navigation.c sel_height): to the set's
 * list end, so the wheel's last row ends there and the rows past it fade. */
int modal_sel_height(void *view, int h) {
    int end = list_fit_end() - screen_y(view);
    return end > 0 && end < h ? end : h;
}

/* A 12 px space in the rows, never a rule; it is no row (nothing to click), so the wheel steps
 * over it. */
void *modal_gap(void *view) {
    void *gap = label_create(view, 0, 0, modal_row_w(), MODAL_GAP);
    widget_set_text_utf8(gap, "");
    return gap;
}

/* The next modal's header: the item's name, the line under it and the cover of record r (0 for
 * none), as Now Playing (the playing song's), the album page or Cover Flow find it. */
void modal_head(const char *title, const char *sub, void *r) {
    head_t *h = &head_next;
    tk_snprintf(h->title, sizeof h->title, "%s", title ? title : "");
    tk_snprintf(h->sub, sizeof h->sub, "%s", sub ? sub : "");
    h->url[0] = 0;
    const char *file = (const char *)0;
    if (!r) return;
    if (now_cover(r, &file) && file && *file) {
        tk_snprintf(h->url, sizeof h->url, "%s", file); /* a file:// URL already */
        return;
    }
    tk_snprintf(h->url, 8, "file://");
    if (!catalogue_cover(P(r, REC_PATH), h->url + 7, sizeof h->url - 7)) {
        art_path(h->url + 7, album_key(r), "");
        if (file_size(h->url + 7) <= 0) h->url[0] = 0;
    }
}

/* Row lays out its label for the sheet and keeps its symbol and value for modal_row_paint: an
 * action's label from the text column to its symbol, a setting's or choice's after its leading
 * symbol, short of the value, a switch's short of the switch. icon 0: none. */
void modal_row(void *row, void *label, const char *icon, const char *value, int how) {
    int x = text_x() + (how == MM_SETTING || how == MM_CHOICE ? MODAL_LEAD : 0),
        end = right_x() - (value              ? 96
                           : icon             ? MODAL_ICON + 8
                           : how == MM_SWITCH ? MODAL_SWITCH + 8
                                              : 0);
    widget_set_prop_str(row, MM_ICON, icon ? icon : "");
    widget_set_prop_str(row, MM_VALUE, value ? value : "");
    widget_set_prop_int(row, MM_HOW, how);
    widget_set_prop_int(row, "style:normal:bg_color", 0);
    if (!label) return;
    widget_move_resize(label, x, 0, end - x, MODAL_ROW);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    if (!tk_strcmp(widget_get_type(label), "hscroll_label")) { /* a long one scrolls when lit */
        hscroll_label_set_only_focus(label, 1);
        hscroll_label_set_ellipses(label, 1);
    }
}

/* Row is destructive (Delete, Remove from Playlist): its words red at rest, white on its red bar
 * (navigation.c paint_selection), as the confirm card's are. */
void modal_red(void *row) { widget_set_prop_int(row, MM_RED, 1); }
int modal_is_red(void *row) { return row && widget_get_prop_int(row, MM_RED, 0); }

static int modal_lit(const void *unused);
/* An open dialog as the sheet: its title bar gone, its list the sheet's rows, the page under it
 * receding (modal_paint). The header's space (MM_HEAD) heads the rows when the modal has a header
 * (modal_head; a picker over another modal takes that one's), so it scrolls away with them; with
 * none the rows start where a page's do. Rows are stacked as they stand, a gap's 12 px included
 * (none when there are more than MODAL_ROWS rows, so the rows scroll, and end, on whole rows), each
 * row MODAL_ROW high and the screen's width. The list runs to the screen's foot, its content
 * padded by what lies past the list end, so the wheel's and a drag's last row both end on it
 * (navigation.c list_pad). The list's forced row height and default go, so the gaps keep theirs
 * (ipod_list_layout stacks each child by its own) and the list never reads as a settings list
 * (navigation.c settings_list), which would move it. A dialog with no list (a playlist's More) has
 * its list_items for rows, placed in it the same way. */
void modal_frame(void *win, void *list) {
    void *view = list ? widget_lookup(list, "scroll_view", 1) : win;
    if (!view) view = list && widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    if (!win || !view) return;
    void *nav = widget_lookup(win, "view_navbar", 1);
    if (nav) widget_set_visible(nav, 0, 0);
    widget_set_prop_str(win, "highlight", "");
    widget_set_prop_int(win, "style:normal:bg_color", 0);
    widget_set_prop_int(win, "style:normal:border_color", 0);
    if (list) {
        widget_set_prop_int(list, "style:normal:bg_color", 0);
        I(list, ROW_HEIGHT) = 0; /* and no default either, or it would read as a settings list */
        I(list, LIST_DEFAULT_ITEM_HEIGHT) = 0;
    }
    int k = 0; /* its own slot, else a free one, else the last */
    while (k < MODAL_MAX && modals[k].win != win) ++k;
    if (k == MODAL_MAX) {
        k = 0;
        while (k < MODAL_MAX - 1 && modals[k].win) ++k;
        widget_on(win, EVT_DESTROY, modal_gone, win);
        modals[k].head = head_next;
        for (int j = 0; !head_next.title[0] && j < MODAL_MAX; ++j)
            if (j != k && modals[j].win) modals[k].head = modals[j].head; /* a picker's */
        head_next.title[0] = head_next.sub[0] = head_next.url[0] = 0;
    }
    int head = modals[k].head.title[0] ? SH_HEAD : 0, wy = screen_y(win),
        top = (head ? SH_CONTENT : SH_BARE) - wy, w = modal_row_w(), y = list ? 0 : top + head,
        all = 0;
    void *space = list ? widget_lookup(view, MM_HEAD, 0) : (void *)0;
    if (list && head && !space) {
        space = label_create(view, 0, 0, w, head);
        widget_set_text_utf8(space, "");
        widget_set_name(space, MM_HEAD);
        widget_restack(space, 0);
    }
    /* a sheet that scrolls has no gaps, so it scrolls, and ends, on whole rows (the user's,
     * audit2 E1: a 12 px gap left the end of Now Playing's More cut) */
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        const char *type = widget_get_type(c);
        all += widget_get_visible(c) && tk_strcmp(type, "label") &&
               (list || !tk_strcmp(type, "list_item"));
    }
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        const char *type = widget_get_type(c);
        int gap = !tk_strcmp(type, "label"), h = c == space ? head : gap ? MODAL_GAP : MODAL_ROW;
        if (gap && c != space && all > MODAL_ROWS) widget_set_visible(c, 0, 0);
        if (!widget_get_visible(c) || (!list && !gap && tk_strcmp(type, "list_item"))) continue;
        widget_move_resize(c, 0, y, w, h);
        y += h;
    }
    if (list) {
        int end = list_fit_end(), content = y + (end > 0 && end < SH_H ? SH_H - end : 0);
        widget_set_prop_int(view, "virtual_h", content);
        widget_set_prop_int(view, "_list_pad", content - y); /* navigation.c LIST_PAD */
        widget_set_prop_int(view, "_list_padded", content);
        widget_move_resize(list, 0, top, w, SH_H - wy - top);
        widget_set_prop_str(view, "self_layout", "default(x=0,y=0,w=100%,h=100%)");
    }
    modals[k].win = win, modals[k].list = list, modals[k].view = view;
    modals[k].top = top + wy, modals[k].high = y;
    st.touch_mode = 0; /* opens on its row, lit, however it was asked for */
    timer_add(modal_lit, (void *)0, 0);
}

/* The modal's row lit from the pass after it opened: a tap that opened it (Now Playing's More)
 * hides the selection as its pointer events finish, after the init. */
static int modal_lit(const void *unused) {
    st.touch_mode = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

/* The screen's own buffer (stock's 375 x 320 lcd_mem, BGRA8888, as engine.c's eng_paint draws on
 * it) under canvas, or 0 for another (a window's snapshot while one slides). */
static unsigned *sheet_fb(void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || !P(lcd, LCD_OFFLINE_FB) || I(lcd, LCD_FORMAT) != LCD_BGRA8888 ||
        I(lcd, LCD_W) != SH_W || I(lcd, LCD_H) != SH_H || I(lcd, LCD_LINE_LENGTH) != 4 * SH_W ||
        window_manager_is_animating(window_manager()))
        return (unsigned *)0;
    return P(lcd, LCD_OFFLINE_FB);
}
/* The canvas's clip on the screen, cut to it: x0, y0, x1, y1. */
static void sheet_clip(void *canvas, int c[4]) {
    int r[4] = { 0, 0, 0, 0 };
    canvas_get_clip_rect(canvas, r);
    c[0] = r[0] < 0 ? 0 : r[0], c[1] = r[1] < 0 ? 0 : r[1];
    c[2] = r[0] + r[2] > SH_W ? SH_W : r[0] + r[2], c[3] = r[1] + r[3] > SH_H ? SH_H : r[1] + r[3];
}

static unsigned px_at(const unsigned *fb, int x, int y) {
    return x < 0 || y < 0 || x >= SH_W || y >= SH_H ? 0 : fb[y * SH_W + x];
}
/* The page receding: where screen pixel d's centre comes from, in 1/256 px, about the centre
 * twice_c / 2 */
static int src_at(int d, int twice_c) {
    return (2 * d + 1 - twice_c) * 128 * 256 / SH_SHRINK + twice_c * 128 - 128;
}
static unsigned bilerp(const unsigned *fb, int sx, int sy) {
    int x = sx >> 8, y = sy >> 8, fx = sx & 255, fy = sy & 255;
    unsigned a = px_at(fb, x, y), b = px_at(fb, x + 1, y), c = px_at(fb, x, y + 1),
             d = px_at(fb, x + 1, y + 1), out = 0xff000000u;
    for (int s = 0; s < 24; s += 8) {
        int t = (int)(a >> s & 255) * (256 - fx) + (int)(b >> s & 255) * fx,
            u = (int)(c >> s & 255) * (256 - fx) + (int)(d >> s & 255) * fx;
        out |= (unsigned)((t * (256 - fy) + u * fy) >> 16) << s;
    }
    return out;
}
static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
/* One plane's box blur, three passes, along n values stride apart */
static void box(int *v, int n, int stride, int *tmp) {
    for (int pass = 0; pass < 3; ++pass) {
        int sum = 0;
        for (int i = 0; i < n; ++i) tmp[i] = v[i * stride];
        for (int k = -SH_BLUR; k <= SH_BLUR; ++k) sum += tmp[clampi(k, 0, n - 1)];
        for (int i = 0; i < n; ++i) {
            v[i * stride] = sum / (2 * SH_BLUR + 1);
            sum += tmp[clampi(i + SH_BLUR + 1, 0, n - 1)] - tmp[clampi(i - SH_BLUR, 0, n - 1)];
        }
    }
}
static unsigned isqrt(unsigned v) {
    unsigned s = v, t = (v + 1) / 2;
    while (t < s) s = t, t = (s + v / s) / 2;
    return s;
}

/* The material from the page under the sheet, whole in fb (its first whole paint): the page
 * receding and dimmed for the strip above the sheet, then that page blurred on a quarter-size grid,
 * scaled back, its colour raised, darkened and tinted (Light Mode: under white at 75%), with a
 * grain of +-3 levels, for the sheet. ponytail: made once, so a page that changes under an open
 * sheet (Now Playing's time) shows as it was; making it again on each whole paint would follow. */
static void sheet_build(const unsigned *fb) {
    unsigned n = (SH_STRIP + SH_H - SH_TOP) * SH_W;
    int *q = calloc(3 * SH_QW * SH_QH + SH_QW + SH_QH, sizeof *q), light = ipod_light();
    fr.buf = calloc(n, sizeof *fr.buf);
    if (!q || !fr.buf) {
        free(q), free(fr.buf), fr.buf = (unsigned *)0;
        return;
    }
    int keep = light ? SH_PAGE_L : SH_PAGE;
    for (int y = 0; y < SH_STRIP; ++y)
        for (int x = 0, sy = src_at(y, SH_H); x < SH_W; ++x) {
            unsigned c = bilerp(fb, src_at(x, SH_W), sy);
            fr.buf[y * SH_W + x] = 0xff000000u | ((c >> 16 & 255) * keep >> 8) << 16 |
                                   ((c >> 8 & 255) * keep >> 8) << 8 | ((c & 255) * keep >> 8);
        }
    int *pl[3] = { q, q + SH_QW * SH_QH, q + 2 * SH_QW * SH_QH }, *tmp = q + 3 * SH_QW * SH_QH;
    for (int qy = 0; qy < SH_QH; ++qy)
        for (int qx = 0; qx < SH_QW; ++qx) {
            int sum[3] = { 0, 0, 0 };
            for (int j = 0; j < 4; ++j)
                for (int i = 0; i < 4; ++i) {
                    unsigned c = px_at(fb, (src_at(4 * qx + i, SH_W) + 128) >> 8,
                                       (src_at(4 * qy + j, SH_H) + 128) >> 8);
                    sum[0] += c >> 16 & 255, sum[1] += c >> 8 & 255, sum[2] += c & 255;
                }
            for (int p = 0; p < 3; ++p) pl[p][qy * SH_QW + qx] = sum[p] / 16;
        }
    for (int p = 0; p < 3; ++p) {
        for (int qy = 0; qy < SH_QH; ++qy) box(pl[p] + qy * SH_QW, SH_QW, 1, tmp);
        for (int qx = 0; qx < SH_QW; ++qx) box(pl[p] + qx, SH_QH, SH_QW, tmp);
    }
    unsigned *mat = fr.buf + SH_STRIP * SH_W;
    for (int y = SH_TOP; y < SH_H; ++y) {
        int fy = clampi(64 * y - 96, 0, (SH_QH - 1) * 256), y0 = fy >> 8,
            y1 = y0 + (y0 < SH_QH - 1);
        fy &= 255;
        for (int x = 0; x < SH_W; ++x) {
            int fx = clampi(64 * x - 96, 0, (SH_QW - 1) * 256), x0 = fx >> 8,
                x1 = x0 + (x0 < SH_QW - 1), c[3];
            fx &= 255;
            for (int p = 0; p < 3; ++p) {
                const int *v = pl[p];
                int t = v[y0 * SH_QW + x0] * (256 - fx) + v[y0 * SH_QW + x1] * fx,
                    u = v[y1 * SH_QW + x0] * (256 - fx) + v[y1 * SH_QW + x1] * fx;
                c[p] = (t * (256 - fy) + u * fy) >> 16;
            }
            int luma = (77 * c[0] + 150 * c[1] + 29 * c[2]) >> 8,
                grain = (int)(((unsigned)x * 73856093u ^ (unsigned)y * 19349663u) >> 8) % 7 - 3;
            unsigned out = 0xff000000u;
            for (int p = 0; p < 3; ++p) {
                int v = clampi(luma + ((c[p] - luma) * SH_SAT >> 8), 0, 255);
                v = light ? (v * (256 - SH_TINT_L) + 255 * SH_TINT_L) >> 8
                          : ((v * SH_KEEP >> 8) * (256 - SH_TINT) + 255 * SH_TINT) >> 8;
                out |= (unsigned)clampi(v + grain, 0, 255) << (16 - 8 * p);
            }
            mat[(y - SH_TOP) * SH_W + x] = out;
        }
    }
    free(q);
    for (int i = 0; i < SH_R; ++i) { /* the corner's inset at row i's middle, in 1/16 px */
        int d = 2 * SH_R - 2 * i - 1;
        fr.edge[i] =
            (unsigned short)(16 * SH_R - (int)isqrt((unsigned)(64 * (4 * SH_R * SH_R - d * d))));
    }
}

static unsigned sheet_mix(unsigned under, unsigned over, int a) { /* over at a/256 on under */
    unsigned out = 0xff000000u;
    for (int s = 0; s < 24; s += 8) {
        int u = (int)(under >> s & 255), o = (int)(over >> s & 255);
        out |= (unsigned)(u + ((o - u) * a >> 8)) << s;
    }
    return out;
}

/* The page receding and the sheet, under an open modal's rows; the C2 sheet (flat, the page
 * dimmed in place) until the material is made, and on a window's snapshot. */
void modal_paint(void *w, void *canvas) {
    int i = 0;
    while (i < MODAL_MAX && (!w || modals[i].win != w)) ++i;
    /* a modal whose rows hide under a picker (modal_pick) draws neither */
    if (i == MODAL_MAX || !P(canvas, CANVAS_LCD) ||
        (modals[i].list && !widget_get_visible(modals[i].list)))
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), *fb = sheet_fb(canvas);
    int c[4], light = ipod_light(), wy = screen_y(w);
    sheet_clip(canvas, c);
    if (fb && !fr.buf && !c[0] && !c[1] && c[2] == SH_W && c[3] == SH_H) sheet_build(fb);
    if (fb && fr.buf) {
        const unsigned *mat = fr.buf + SH_STRIP * SH_W;
        for (int y = c[1]; y < c[3]; ++y) {
            unsigned *d = fb + y * SH_W;
            const unsigned *s = fr.buf + y * SH_W, *m = mat + (y - SH_TOP) * SH_W;
            if (y < SH_TOP) {
                for (int x = c[0]; x < c[2]; ++x) d[x] = s[x];
            } else if (y < SH_TOP + SH_R) {
                int e = fr.edge[y - SH_TOP];
                for (int x = c[0]; x < c[2]; ++x) {
                    int l = clampi(16 * (x + 1) - e, 0, 16), r = clampi(16 * (SH_W - x) - e, 0, 16);
                    d[x] = sheet_mix(s[x], m[x], 16 * (l < r ? l : r));
                }
            } else
                for (int x = c[0]; x < c[2]; ++x) d[x] = m[x];
        }
    } else {
        rect_t sheet = { 0, SH_TOP - wy, SH_W, SH_H + SH_R };
        unsigned dim = (unsigned)(256 - (light ? SH_PAGE_L : SH_PAGE)) << 24;
        for (int y = 0; y < SH_TOP - wy + SH_R; ++y) scrub_row(canvas, dim, 0, y, SH_W);
        for (int y = 0; y < SH_H - SH_TOP; ++y)
            round_row(canvas, &sheet, SH_R, y, 0xff000000u | flip_if(light, MODAL_FILL));
    }
    rect_t grab = { SH_W / 2 - 18, SH_GRAB_Y - wy, 36, 5 };
    fill_box(canvas, &grab, RGBA(light ? SH_GRAB_L : SH_GRAB), 2);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Over an open modal's rows once they are painted (navigation.c ringnav_paint), w its list's
 * scroll_view or a dialog of rows alone: the header at the rows' head, scrolled with them, then
 * the rows faded into the material past the list end and, once scrolled, under the header's place,
 * the lit row never. */
void modal_after(void *w, void *canvas) {
    int i = 0;
    while (i < MODAL_MAX &&
           (!w || (modals[i].view != w && !(modals[i].win == w && !modals[i].list))))
        ++i;
    if (i == MODAL_MAX || !P(canvas, CANVAS_LCD)) return;
    const head_t *h = &modals[i].head;
    int scroll = modals[i].list ? I(w, SCROLL_Y) : 0, top = modals[i].top - scroll,
        ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y), light = ipod_light(), c[4];
    unsigned *fb = sheet_fb(canvas);
    sheet_clip(canvas, c);
    /* only within the rows' box: the canvas's clip may be the window's */
    int box0 = modals[i].list ? screen_y(w) : SH_TOP,
        box1 = modals[i].list ? box0 + I(modals[i].list, W_H) : SH_H; /* the view fills it */
    c[1] = c[1] > box0 ? c[1] : box0, c[3] = c[3] < box1 ? c[3] : box1;
    if (c[3] <= c[1]) return;
    int was[4];
    canvas_get_clip_rect(canvas, was);
    rect_t in = { c[0], c[1], c[2] - c[0], c[3] - c[1] };
    canvas_set_clip_rect(canvas, &in);
    if (h->title[0] && top + SH_HEAD > c[1]) {
        rect_t art = { text_x() - ox, top + 6 - oy, SH_ART, SH_ART };
        unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
        if (h->url[0] && !widget_load_image(w, h->url, bitmap) && bitmap[0] && bitmap[1]) {
            int bw = (int)bitmap[0], bh = (int)bitmap[1], side = bw < bh ? bw : bh;
            rect_t src = { (bw - side) / 2, (bh - side) / 2, side, side };
            canvas_draw_image(canvas, bitmap, &src, &art);
            for (int r = 0; fb && fr.buf && r < 6; ++r) { /* its corners the material's */
                int n = round_inset(6, r, SH_ART);
                for (int b = 0; b < 2; ++b) {
                    int y = top + 6 + (b ? SH_ART - 1 - r : r), x0 = text_x();
                    if (y < c[1] || y >= c[3] || y < SH_TOP) continue;
                    for (int k = 0; k < n; ++k)
                        for (int side2 = 0; side2 < 2; ++side2) {
                            int x = side2 ? x0 + SH_ART - 1 - k : x0 + k;
                            if (x >= c[0] && x < c[2])
                                fb[y * SH_W + x] = fr.buf[(SH_STRIP + y - SH_TOP) * SH_W + x];
                        }
                }
            }
        } else
            fill_box(canvas, &art, RGBA(flip_if(light, 0x2c2c2e)), 6);
        int tw = right_x() - SH_NAME_X;
        ar_text(canvas, h->title, 1, 24, 0xffffff, 0, SH_NAME_X - ox, top + 22 - oy, tw, 2);
        if (h->sub[0])
            ar_text(canvas, h->sub, 0, 18, MODAL_SECONDARY, 0, SH_NAME_X - ox, top + 44 - oy, tw,
                    2);
    }
    canvas_set_clip_rect(canvas, (rect_t *)was);
    if (!fb || !fr.buf) return;
    int list_top = box0, s0 = 1, s1 = 0;
    if (st.sel_w == w) s0 = screen_y(w) + st.sel_y - st.sel_h / 2, s1 = s0 + st.sel_h;
    const unsigned *mat = fr.buf + SH_STRIP * SH_W;
    for (int y = c[1]; y < c[3]; ++y) {
        int a = y >= SH_FOOT ? foot_alpha[y - SH_FOOT] + 1 : 256;
        if (scroll > 0 && y < list_top + SH_FADE_TOP) {
            int t = (y - list_top) * 256 / SH_FADE_TOP;
            if (t < a) a = t;
        }
        if (a >= 256 || (y >= s0 && y < s1)) continue;
        unsigned *d = fb + y * SH_W;
        const unsigned *m = mat + (y - SH_TOP) * SH_W;
        for (int x = c[0]; x < c[2]; ++x) d[x] = sheet_mix(m[x], d[x], a < 0 ? 0 : a);
    }
}

static int modal_ink(void *canvas, const unsigned *s, unsigned n) {
    int px = theme()->font_px[ROLE_TITLE];
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

/* s (ASCII) right-aligned on column right of r, in the row's face and size, in a final colour;
 * at most room wide, ending in an ellipsis where it is wider. */
static void value_text(void *canvas, const char *s, int right, int room, const rect_t *r,
                       unsigned rgb) {
    unsigned buf[40], n = 0;
    while (s[n] && n < sizeof buf / sizeof *buf - 1) buf[n] = (unsigned char)s[n], ++n;
    buf[n] = 0;
    int ink = modal_ink(canvas, buf, n);
    while (n > 1 && ink > room) buf[--n - 1] = 0x2026, buf[n] = 0, ink = modal_ink(canvas, buf, n);
    rect_t box = { r->x + right - ink, r->y, ink + 1, r->h };
    draw_centred(canvas, buf, n, &box, (unsigned)theme()->font_px[ROLE_TITLE], RGBA(rgb));
}

static void icon_at(void *w, void *canvas, const char *stem, const char *ink, int x,
                    const rect_t *r) {
    char name[48];
    tk_snprintf(name, sizeof name, "menu_%s_%s", stem, ink);
    paint_art(w, canvas, theme_art(name), r->x + x, r->y + (r->h - MODAL_ICON) / 2);
}

/* One row of an open modal (navigation.c paint_modal_rows): its symbol and value, white on the
 * selection, Text Secondary off it; a choice's check before its label in the accent. */
void modal_row_paint(void *w, void *canvas, void *row, rect_t r, int selected) {
    const char *icon = widget_get_prop_str(row, MM_ICON, ""),
               *value = widget_get_prop_str(row, MM_VALUE, "");
    if (!icon) icon = "";
    if (!value) value = "";
    int how = widget_get_prop_int(row, MM_HOW, MM_ACTION), light = ipod_light();
    int right = right_x(), lead = text_x(), raw = st.raw_ink;
    if (modal_is_red(row)) { /* its words red at rest, white on its bar: set once each changes */
        int want = (int)RGBA(selected ? 0xffffff : MODAL_RED);
        void *at = row; /* a playlist's More: its label in the item's button */
        for (unsigned i = 0, n = widget_count_children(row); i < n && at == row; ++i)
            if (!tk_strcmp(widget_get_type(widget_get_child(row, i)), "button"))
                at = widget_get_child(row, i);
        for (unsigned i = 0, n = widget_count_children(at); i < n; ++i) {
            void *c = widget_get_child(at, i);
            if (!tk_strcmp(widget_get_type(c), "hscroll_label") &&
                widget_get_prop_int(c, "_mm_ink", 0) != want) {
                widget_set_prop_int(c, "_mm_ink", want);
                widget_set_prop_int(c, "style:normal:text_color", want);
            }
        }
    }
    if (how == MM_SWITCH) { /* by stock's own switch image, hidden on the row */
        for (unsigned i = 0, n = widget_count_children(row); i < n; ++i) {
            const char *image = widget_get_prop_str(widget_get_child(row, i), "image", "");
            if (image && !strncmp(image, "switch_", 7))
                switch_mark(canvas, r.x + right, r.y + r.h / 2, !tk_strcmp(image, "switch_on"),
                            selected);
        }
        return;
    }
    if (!*icon && !*value) return;
    const char *ink = selected ? "w" : light ? "l" : "d";
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    if (*icon && how != MM_ACTION)
        icon_at(w, canvas, icon, how == MM_CHOICE && !selected ? "a" : ink, lead, &r);
    else if (*icon && !*value)
        icon_at(w, canvas, icon, ink, right - MODAL_ICON, &r);
    if (*value) {
        st.raw_ink = 1;
        /* clear of its label's text: a long value gives way */
        int room = right - text_x() - MODAL_LEAD - 16;
        for (unsigned i = 0, n = widget_count_children(row); i < n; ++i) {
            void *c = widget_get_child(row, i);
            const unsigned *t =
                tk_strcmp(widget_get_type(c), "hscroll_label") || !widget_get_visible(c)
                    ? (const unsigned *)0
                    : widget_get_text(c);
            if (t) room = right - I(c, W_X) - modal_ink(canvas, t, wide_len(t)) - 16;
        }
        value_text(canvas, value, right, room, &r,
                   selected ? 0xffffff
                   : light  ? MODAL_SECONDARY_L
                            : MODAL_SECONDARY);
        st.raw_ink = raw;
    }
    stock_canvas_set_text_color(canvas, text);
}

/* A picker in a sheet (Multiple Speed's, ratings.c's): stock's sortselect dialog, its rows
 * rebuilt as names, the current one checked, as the hold menu's choices are (navigation.c qm_open),
 * headed by the sheet it opens over, if any. The rows of the dialog over which it opens, under,
 * hide while it is up, and with them its sheet and header, so one sheet shows; they come back on
 * the picker's close, the selection where it was. The dialog itself stays shown: a hidden window
 * would hand the keys to the page under it. A pick runs from the next loop pass, after the close,
 * with the picker's index. 1 if it opened. */
static struct {
    void (*pick)(int i);
    void *dialog, *under;
    unsigned timer;
    int chosen;
} pk __attribute__((section(".scratch")));

static void pick_close(void) {
    void *d = pk.dialog;
    pk.dialog = (void *)0;
    if (d) window_close(d);
}
static int pick_run(const void *unused) {
    pk.timer = 0;
    if (!pk.dialog) return 0;
    pick_close();
    if (pk.pick) pk.pick(pk.chosen);
    return 0;
}
static int pick_click(void *ctx, void *event) {
    if (pk.dialog && !pk.timer) {
        pk.chosen = (int)(long)ctx;
        pk.timer = timer_add(pick_run, (void *)0, 0);
    }
    return 0;
}
static int pick_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    pick_close();
    return STOP;
}
static int pick_gone(void *dialog, void *event) {
    if (pk.dialog == dialog) pk.dialog = (void *)0;
    if (pk.under)
        widget_set_visible(pk.under, 1, 0), widget_invalidate_force(window_of(pk.under), 0);
    pk.under = (void *)0;
    return 0;
}

static int modal_list(void *under, const char *const *names, const char *const *icons, int n,
                      int current, void (*pick)(int i)) {
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    if (pk.dialog || n <= 0) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    void *back = widget_lookup(dialog, "img_return", 1),
         *view = widget_lookup(dialog, "scroll_view", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, pick_back, dialog);
    widget_on(back, EVT_CLICK, pick_back, dialog);
    widget_on(dialog, EVT_DESTROY, pick_gone, dialog);
    widget_destroy_children(view);
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * MODAL_ROW, modal_row_w(), MODAL_ROW);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 0, 0, 0, MODAL_ROW);
        widget_use_style(label, "s_scrlabel_white24l");
        widget_set_text_utf8(label, names[i]);
        if (icons) modal_row(item, label, icons[i], (const char *)0, MM_ACTION);
        if (icons && icons[i] && !tk_strcmp(icons[i], "trash"))
            modal_red(item); /* destructive */
        else
            modal_row(item, label, i == current ? "check" : (const char *)0, (const char *)0,
                      MM_CHOICE);
        widget_on(item, EVT_CLICK, pick_click, (void *)(long)i);
    }
    pk.dialog = dialog, pk.pick = pick;
    pk.under = under;
    if (under) widget_set_visible(under, 0, 0);
    modal_frame(dialog, widget_lookup(dialog, "list_view", 1));
    ringnav_select(view, current >= 0 && current < n ? current : 0, n);
    return 1;
}
int modal_pick(void *under, const char *const *names, int n, int current, void (*pick)(int i)) {
    return modal_list(under, names, (const char *const *)0, n, current, pick);
}
/* A menu of n actions in a sheet, each its symbol (tools/art.py MENU_ICONS) at the right, as the
 * hold menus' are (an artist's page, addon.c artist_hold), a "trash" one destructive (modal_red);
 * pick gets the chosen one's index. It opens on the first. */
int modal_actions(const char *const *names, const char *const *icons, int n, void (*pick)(int i)) {
    return modal_list((void *)0, names, icons, n, 0, pick);
}

/* Now Playing's More (onlinematch.c ringnav_playermore): a dialog under a modal set
 * (tools/ipod.py modal_page), modeless, as the hold menu's, so navigator_to returns. Its rows as
 * the sheet's, each with its symbol: stock's by their kind (PLAYMORE_ROWS, the button's name its
 * index), AirPlay's, Download Album Lyrics' and About's by their images. The header is the playing song. Delete Song, which asks first, goes
 * last after a gap, red. A-B Repeat's stock switch hides, and the row paints the theme's
 * (switch_mark) on the right column by its image. Multiple Speed is a setting row, its value the
 * speed, opening a picker of the four (M3); a pick is a click on stock's own speed button for it,
 * which the hidden row keeps. The menu opens on its first row. */
static const char *const more_icons[] = { 0,
                                          "speedometer",
                                          "queue",
                                          "list-plus",
                                          "download-simple",
                                          "vinyl-record",
                                          "microphone-stage",
                                          "trash" };
static const char *const speeds[] = { "1x", "1.25x", "1.5x", "2x" };
static void *more_win __attribute__((section(".scratch")));

static void more_speed_pick(int i) {
    char name[16];
    tk_snprintf(name, sizeof name, "img_forward_%d", i);
    void *b = more_win ? widget_lookup(more_win, name, 1) : (void *)0;
    char click[0x30];
    if (b) stock_dispatch_trampoline(b, pointer_event_init(click, EVT_CLICK, b, 0, 0));
    void *row = more_win ? widget_lookup(more_win, "_mm_speed", 1) : (void *)0;
    if (row) widget_set_prop_str(row, MM_VALUE, speeds[g_playspeed_mode & 3]);
}
static int more_speed(void *win, void *event) {
    more_win = win;
    modal_pick(widget_lookup(win, "list_view_more", 1), speeds, 4, g_playspeed_mode & 3,
               more_speed_pick);
    return STOP;
}
/* Return closes the dialog: stock's key-up (on_common_keyup) goes back a page, which would close
 * the page under it and leave the menu over the one before. */
#define COMMON_KEYUP 0x4f2394
static int modal_back(void *win, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if (!widget_get_prop_int(win, "_mm_closing", 0)) { /* the list's and the dialog's: once */
        widget_set_prop_int(win, "_mm_closing", 1);
        window_close(win);
    }
    return STOP;
}
/* ... on stock's list w and on the dialog, which has the keys when nothing in it has the focus
 * (back from a picker) */
static void modal_return(void *w, void *win) {
    widget_off_by_func(w, EVT_KEY_UP, (void *)COMMON_KEYUP, win);
    widget_on(w, EVT_KEY_UP, modal_back, win);
    if (w != win) widget_on(win, EVT_KEY_UP, modal_back, win);
}

/* A switch row's flip, which stock draws on its hidden image: the row shows it. */
static int switch_flipped(void *row, void *event) {
    widget_invalidate_force(row, (void *)0);
    return 0;
}

static int more_gone(void *win, void *event) {
    if (more_win == win) more_win = (void *)0;
    return 0;
}

void modal_more(void *win) {
    void *list = widget_lookup(win, "list_view_more", 1),
         *view = list ? widget_lookup(list, "scroll_view_more", 1) : (void *)0;
    if (!view || tk_strcmp(widget_get_type(win), "dialog")) return;
    widget_set_prop_int(win, "to_modal", 0);
    modal_return(list, win); /* stock binds it on the list, its context the page */
    more_win = win;
    widget_on(win, EVT_DESTROY, more_gone, win);
    void *last = (void *)0;
    unsigned n = widget_count_children(view);
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i),
             *button = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        if (!button) continue;
        const char *name = widget_get_prop_str(button, "name", ""), *icon = (const char *)0;
        int kind = -1, how = MM_ACTION;
        if (!name) name = ""; /* an unnamed widget's name is null */
        void *label = (void *)0, *sw = (void *)0;
        if (*name >= '0' && *name <= '9' && (unsigned)(*name - '0') < PLAYMORE_COUNT)
            kind = ((const int *)PLAYMORE_ROWS)[*name - '0'];
        for (unsigned j = 0, m = widget_count_children(button); j < m; ++j) {
            void *c = widget_get_child(button, j);
            const char *type = widget_get_type(c);
            if (!tk_strcmp(type, "hscroll_label")) {
                if (label)
                    widget_set_visible(c, 0, 0); /* Speed's note under it */
                else
                    label = c;
            } else if (!tk_strcmp(type, "image")) {
                const char *image = widget_get_prop_str(c, "image", "");
                if (!image) image = "";
                if (!tk_strcmp(image, "airplay")) icon = "airplay";
                if (!tk_strcmp(image, "player_onlinesearch")) icon = "cloud-arrow-down";
                if (!tk_strcmp(image, "system_about")) icon = "info";
                if (!strncmp(image, "switch_", 7))
                    sw = c;
                else
                    widget_set_visible(c, 0, 0); /* stock's icons; Speed's buttons */
            }
        }
        if (widget_lookup(button, "img_forward_0", 1)) kind = 1; /* the speed row, unnamed */
        if (kind >= 0 && kind < (int)(sizeof more_icons / sizeof *more_icons))
            icon = more_icons[kind];
        widget_move_resize(button, 0, 0, modal_row_w(), MODAL_ROW);
        if (kind == 1) { /* Multiple Speed */
            how = MM_SETTING;
            widget_set_prop_str(button, "name", "_mm_speed");
            widget_on(button, EVT_CLICK, more_speed, win);
            if (label) widget_set_text_utf8(label, "Speed");
        }
        if (sw) { /* A-B Repeat: the theme's switch in its place (rule 7), by its state */
            how = MM_SWITCH;
            widget_set_visible(sw, 0, 0);
            widget_on(button, EVT_CLICK, switch_flipped,
                      button); /* after stock's, which flips it */
        }
        modal_row(button, label, icon, kind == 1 ? speeds[g_playspeed_mode & 3] : (const char *)0,
                  how);
        if (kind == PLAYMORE_DELETE) modal_red(button);
        if (kind == PLAYMORE_DELETE && widget_get_visible(item)) last = item;
    }
    if (last) { /* last, after a gap */
        widget_restack(last, n - 1);
        widget_restack(modal_gap(view), n - 1);
    }
    unsigned pos, count;
    void *r = queue_now(&pos, &count); /* the playing song */
    if (r) modal_head(now_tag(r, REC_TITLE), now_tag(r, REC_ARTIST), r);
    modal_frame(win, list);
    ringnav_select(view, 0, (int)widget_count_children(view));
}

/* A playlist's More (playlists.c ringnav_playlistsmore), a dialog over Playlists under a modal
 * set as Now Playing's is: Rename and Edit Playlist, then Delete Playlist after a gap, red, which
 * asks first (stock's confirm). Its rows are list_items in the dialog itself, placed in the sheet;
 * its header the playlist's name (playlists.c). */
void modal_playlist_more(void *win) {
    if (tk_strcmp(widget_get_type(win), "dialog")) return;
    widget_set_prop_int(win, "to_modal", 0);
    modal_return(win, win);
    void *del = (void *)0;
    unsigned n = widget_count_children(win);
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(win, i),
             *button = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        if (!button || tk_strcmp(widget_get_type(item), "list_item")) continue;
        void *label = (void *)0;
        for (unsigned j = 0, m = widget_count_children(button); j < m; ++j) {
            void *c = widget_get_child(button, j);
            if (!tk_strcmp(widget_get_type(c), "hscroll_label"))
                label = c;
            else
                widget_set_visible(c, 0, 0); /* stock's icons */
        }
        for (unsigned j = 0, m = widget_count_children(item); j < m; ++j)
            if (widget_get_child(item, j) != button)
                widget_set_visible(widget_get_child(item, j), 0, 0);
        const char *name = widget_get_prop_str(item, "name", "");
        if (!name) name = "";
        int is_del = !tk_strcmp(name, "item_del");
        const char *icon = is_del                            ? "trash"
                           : !tk_strcmp(name, "item_rename") ? "pencil-simple"
                                                             : "list-checks";
        widget_move_resize(button, 0, 0, modal_row_w(), MODAL_ROW);
        modal_row(button, label, icon, (const char *)0, MM_ACTION);
        modal_row(item, (void *)0, icon, (const char *)0, MM_ACTION); /* stock's rows click there */
        if (is_del) modal_red(button), modal_red(item);
        if (is_del && widget_get_visible(item)) del = item;
    }
    if (del) { /* last, after a gap */
        widget_restack(del, n - 1);
        widget_restack(modal_gap(win), n - 1);
    }
    modal_frame(win, (void *)0);
}
