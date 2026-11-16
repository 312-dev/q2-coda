/* The Library's row order and OLED's Edit Library (docs/oled-design.md#edit-library,
 * docs/internals.md#library-order). Each Library row carries a letter (LR_PROP, home.c
 * ringnav_localmusic) that names it in the saved order, LR_FILE: those letters in the user's order,
 * replaced whole (blob_io: written beside it, synced, renamed over it), so a power cut leaves the
 * old order or the new one. lr_resolve reads it against lr_default, the rows' default order, the
 * one table of it: a letter it doesn't know is dropped, and a row it doesn't name (one a later
 * firmware adds) goes in at its default place, after the row before it there.
 *
 * Under OLED, Centre held LR_HOLD_MS on the Library page, or a finger held as long on a row, enters
 * Edit Library: the status row reads "Edit Library", each row's chevron gives way to a grip, and a
 * hint shows under the rows. A finger on a row's grip (the last LR_GRIP_ZONE px) drags it at once,
 * a finger held on a row drags it after LR_HOLD_MS; the rows it passes slide aside over
 * LR_SLIDE_MS, the list scrolls while the finger is within LR_EDGE px of its top or bottom, and the
 * lift puts the row in its place and saves. Centre lifts the selected row (drawn larger, in
 * Inter Medium, on a raised card), the wheel carries it, Centre puts it down and saves; Return
 * while it is carried puts it back where it was. Return, or Centre held again, saves and leaves
 * Edit Library. A hidden row (stock's My Fav and Frequent, a media row without its folder) is
 * neither shown nor moved: the shown rows' new order fills the places they held (lr_merge). */
#include "navigation.h"

#define LR_FILE "/mnt/data/ringnav-library-order"
#define LR_PROP "_lr_code"
#define LR_MAX 64       /* the saved order's bytes, its letters and a NUL: room for every row */
#define LR_HOLD_MS 550  /* Centre or a finger held this long enters (or leaves) Edit Library */
#define LR_SLIDE_MS 150 /* the rows a moved row passes slide aside over this */
#define LR_STEP_MS 16   /* ... a frame at a time */
#define LR_SLOP 8       /* a finger that moves this far before its hold is a scroll */
#define LR_GRIP_ZONE 64 /* a press this near the screen's right edge is on the grip */
#define LR_EDGE 16      /* a dragged row this near the list's top or bottom scrolls it */
#define LR_SCROLL_PX 8  /* ... this far */
#define LR_SCROLL_MS 40 /* ... this often */
#define LR_GRIP_W 18    /* the grip: three LR_GRIP_H px bars LR_GRIP_GAP apart, centred on the */
#define LR_GRIP_H 2     /* chevron's place, 5 px left of the right column */
#define LR_GRIP_GAP 5
#define LR_LIFT 108 /* the lifted row's card: the selection bar's box grown to 108% */
#define LR_CARD_RGB 0x2c2c2e
#define LR_HINT_PX 16 /* the hint: Inter Regular 13 px (AWTK's line size, 13 x 1.21) */
#define LR_HINT_Y 298 /* its middle, 14 px above the screen's foot */
#define LR_LIFT_PX 27 /* the lifted row's words: Inter Medium 22 px, 20 px grown 8% */

/* The Library's rows in their default order (the menu review's, 2026-11-15), each its letter in
 * LR_FILE: Shuffle (hidden), Songs, Playlists, Artists, Albums, Search, Genres, Years, Ratings, My
 * Fav (hidden), Recently Added, Recent, Most Played, Frequent (hidden), Hi-Res (hidden), Long Songs
 * (hidden), Podcasts, Audiobooks, Videos, Photos, Books, Games, Upload Scrobbles (hidden), Update
 * Local Music. A letter is never reused: a new row takes a new one, here where it goes by default. */
const char lr_default[] = "hspalSgerfncmqiLPAvoBGUu";
_Static_assert(sizeof lr_default <= LR_ROWS, "a row per letter");

static struct {
    void *page, *view;    /* the Library page in Edit Library and its rows' view, 0 when not */
    void *lifted;         /* the row Centre lifted or a finger drags */
    void *held;           /* the row a finger holds, LR_HOLD_MS not yet up */
    void *watch;          /* ... and its page */
    unsigned hold;        /* Centre's or that finger's hold timer */
    unsigned slide;       /* the rows' slide, a frame at a time */
    unsigned scroll;      /* a dragged row's scroll at the list's edge */
    unsigned slide_at;    /* the slide's start, time_now_ms */
    int from;             /* the lifted row's place in the view when Centre lifted it */
    int x, y;             /* the finger, on screen */
    int grab;             /* the finger's distance below the dragged row's top */
    unsigned char centre; /* Centre's hold went off: its release is taken */
    unsigned char drag;   /* a finger drags lifted */
    unsigned char quiet;  /* the abort lr_grab sends is its own */
} lr __attribute__((section(".scratch")));

/* Letter c's place in lr_default, -1 if it names no row. */
int lr_at(int c) {
    for (int i = 0; lr_default[i]; ++i)
        if (lr_default[i] == c) return i;
    return -1;
}

static int has(const char *s, int n, int c) {
    for (int i = 0; i < n; ++i)
        if (s[i] == c) return i;
    return -1;
}

/* The saved order (NUL-ended) as the rows go: its known letters, each once, then every row it
 * doesn't name at its default place, after the nearest row before it in lr_default (first, if
 * none). out holds LR_MAX. Returns its length. */
int lr_resolve(const char *saved, char *out) {
    int n = 0;
    for (int i = 0; saved[i] && i < LR_MAX - 1; ++i)
        if (lr_at(saved[i]) >= 0 && has(out, n, saved[i]) < 0) out[n++] = saved[i];
    for (int i = 0; lr_default[i]; ++i) {
        if (has(out, n, lr_default[i]) >= 0) continue;
        int at = 0;
        for (int j = i; j-- > 0;) {
            int k = has(out, n, lr_default[j]);
            if (k >= 0) {
                at = k + 1;
                break;
            }
        }
        for (int k = n++; k > at; --k) out[k] = out[k - 1];
        out[at] = lr_default[i];
    }
    out[n] = 0;
    return n;
}

/* full with the shown rows in their new order (shown, NUL-ended): the places shown's rows held
 * take them in turn, and every other row (hidden, or not on this page) keeps its own. */
void lr_merge(char *full, const char *shown) {
    int n = (int)strlen(shown);
    for (int i = 0, k = 0; full[i] && k < n; ++i)
        if (has(shown, n, full[i]) >= 0) full[i] = shown[k++];
}

/* The order the Library's rows go in: LR_FILE's, resolved. */
static void lr_load(char *out) {
    char saved[LR_MAX];
    BLOB_IO(LR_FILE, saved, 0);
    saved[LR_MAX - 1] = 0;
    lr_resolve(saved, out);
}

/* ringnav_localmusic's row for letter c, tagged. */
void lr_put(void **rows, int c, void *row) {
    int at = lr_at(c);
    if (at < 0 || !row) return;
    rows[at] = row;
    prop(row, LR_PROP, c);
}

/* The view's rows, by their letters, in the saved order. */
void lr_apply(void **rows) {
    char order[LR_MAX];
    lr_load(order);
    for (unsigned i = 0, n = 0; order[i]; ++i) {
        void *row = rows[lr_at(order[i])];
        if (row) widget_restack(row, n++);
    }
}

/* The shown rows' letters in view's order, saved over the places they held. */
static void lr_save(void *view) {
    char full[LR_MAX] = { 0 }, shown[LR_MAX];
    int n = 0;
    for (unsigned i = 0, k = widget_count_children(view); i < k && n < LR_MAX - 1; ++i) {
        void *row = widget_get_child(view, i);
        int c = widget_get_prop_int(row, LR_PROP, 0);
        if (c && widget_get_visible(row)) shown[n++] = (char)c;
    }
    shown[n] = 0;
    lr_load(full);
    lr_merge(full, shown);
    BLOB_IO(LR_FILE, full, 1);
}

/* The rows the view shows, in order (out holds LR_ROWS); their count. */
static int lr_rows(void *view, void **out) {
    int n = 0;
    for (unsigned i = 0, k = widget_count_children(view); i < k && n < LR_ROWS; ++i) {
        void *row = widget_get_child(view, i);
        if (widget_get_visible(row) && widget_get_prop_int(row, LR_PROP, 0)) out[n++] = row;
    }
    return n;
}
static int lr_index(void **rows, int n, void *row) {
    for (int i = 0; i < n; ++i)
        if (rows[i] == row) return i;
    return -1;
}
static int lr_child(void *row) {
    void *view = P(row, W_PARENT);
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i)
        if (widget_get_child(view, i) == row) return (int)i;
    return -1;
}

/* The Library page's rows' view when it is on top and takes input, else 0. */
static void *lr_view_of(void *top) {
    if (!top || !usable() || tk_strcmp(widget_get_prop_str(top, "name", ""), "localmusic_page"))
        return (void *)0;
    return widget_lookup(top, "scroll_view_localmusic", 1);
}

/* The view's top on screen and the height its rows show in: to the set's list end, where a
 * selected row stops (docs/oled-design.md#corners), which counts from the view's window (the
 * Library's starts under the status row), as navigation.c sel_height reads it. */
static int lr_box(void *view, int *h) {
    int y = 0, win = 0, end = geom_val(V_LIST_END);
    for (void *p = view, *wm = window_manager(); p && p != wm; p = P(p, W_PARENT))
        if (P(p, W_PARENT) == wm)
            win = I(p, W_Y);
        else
            y += I(p, W_Y);
    *h = end && end - y < I(view, W_H) ? end - y : I(view, W_H);
    return win + y;
}

/* One frame of the slide: each row from where it showed towards its place, cubic out. */
static int lr_slide_step(const void *info) {
    (void)info;
    if (!lr.view) return lr.slide = 0;
    unsigned t = (unsigned)time_now_ms() - lr.slide_at;
    int left = t >= LR_SLIDE_MS ? 0 : 1024 - (int)(t * 1024 / LR_SLIDE_MS);
    int ease = 1024 - left * left / 1024 * left / 1024;
    for (unsigned i = 0, n = widget_count_children(lr.view); i < n; ++i) {
        void *row = widget_get_child(lr.view, i);
        if (row == lr.lifted && lr.drag) continue; /* the finger's */
        int from = widget_get_prop_int(row, "_lr_from", I(row, W_Y)),
            to = widget_get_prop_int(row, "_lr_to", I(row, W_Y));
        I(row, W_Y) = from + (to - from) * ease / 1024;
    }
    widget_invalidate_force(lr.page, (void *)0);
    if (left) return 8; /* RET_REPEAT */
    lr.slide = 0;
    return 0;
}

/* row to place k among the view's children; the rows between slide from where they show to their
 * new places, row itself goes at once. */
static void lr_place(void *row, int k) {
    void *view = lr.view;
    unsigned n = widget_count_children(view);
    if (n > LR_ROWS) return;
    int was[LR_ROWS];
    for (unsigned i = 0; i < n; ++i) was[i] = I(widget_get_child(view, i), W_Y);
    void *kids[LR_ROWS];
    for (unsigned i = 0; i < n; ++i) kids[i] = widget_get_child(view, i);
    widget_restack(row, (unsigned)k);
    widget_layout(view);
    for (unsigned i = 0; i < n; ++i) {
        void *c = kids[i];
        prop(c, "_lr_to", I(c, W_Y));
        prop(c, "_lr_from", c == row ? I(c, W_Y) : was[i]);
        if (c != row) I(c, W_Y) = was[i];
    }
    lr.slide_at = (unsigned)time_now_ms();
    if (!lr.slide) lr.slide = timer_add(lr_slide_step, (void *)0, LR_STEP_MS);
    widget_invalidate_force(lr.page, (void *)0);
}

/* row to shown place b, its neighbours sliding aside; 1 if it moved. */
static int lr_move(void *row, int b) {
    void *rows[LR_ROWS];
    int n = lr_rows(lr.view, rows), a = lr_index(rows, n, row);
    if (a < 0 || b < 0 || b >= n || a == b) return 0;
    lr_place(row, lr_child(rows[b]));
    return 1;
}

/* The status row's title follows the page in or out of Edit Library. */
static void lr_title(void *page) {
    title_gone((void *)0, (void *)0);
    bar_title_sync(page);
    widget_invalidate_force(page, (void *)0);
}

static void lr_reset(void) {
    stop_timer(&lr.hold);
    stop_timer(&lr.slide);
    stop_timer(&lr.scroll);
    lr.page = lr.view = lr.lifted = lr.held = lr.watch = (void *)0;
    lr.drag = lr.centre = 0;
}

/* The page's close, and while a finger is down its moves and lift (bound once a page, as az_event
 * is): a finger waiting out its hold lets it go by moving LR_SLOP or lifting; one dragging a row
 * moves it and drops it, and its events stop here, so the list neither scrolls nor takes a tap. */
static void lr_drag(void);
static void lr_drop(void);
static int lr_event(void *page, void *event) {
    unsigned type = I(event, EVENT_TYPE);
    if (type == EVT_DESTROY || type == EVT_WINDOW_CLOSE) {
        if (lr.page == page || lr.watch == page) lr_reset();
        return 0;
    }
    if (lr.quiet) return 0;
    if (lr.held && page == lr.watch) {
        int dx = I(event, EVENT_X) - lr.x, dy = I(event, EVENT_Y) - lr.y;
        if (type != EVT_POINTER_MOVE_BEFORE || dx * dx + dy * dy > LR_SLOP * LR_SLOP) {
            lr.held = (void *)0;
            stop_timer(&lr.hold);
        }
        return 0;
    }
    if (!lr.drag || page != lr.page) return 0;
    lr.x = I(event, EVENT_X), lr.y = I(event, EVENT_Y);
    if (type == EVT_POINTER_MOVE_BEFORE)
        lr_drag();
    else
        lr_drop();
    return STOP;
}
static int lr_bind(void *page) {
    if (widget_get_prop_int(page, "_lr_bound", 0)) return 1;
    static const unsigned types[] = { EVT_DESTROY, EVT_WINDOW_CLOSE, EVT_POINTER_ABORT,
                                      EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE };
    for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
        if (!widget_on(page, types[i], lr_event, page)) {
            while (i) widget_off_by_func(page, types[--i], lr_event, page);
            return 0;
        }
    prop(page, "_lr_bound", 1);
    return 1;
}

static void lr_enter(void *page, void *view) {
    if (!lr_bind(page)) return;
    lr.page = page, lr.view = view;
    lr_title(page);
}

/* Out of Edit Library, the order saved; a row still carried stays where it is. */
static void lr_leave(void) {
    void *page = lr.page, *view = lr.view;
    lr_save(view);
    stop_timer(&lr.slide);
    stop_timer(&lr.scroll);
    lr.page = lr.view = lr.lifted = (void *)0;
    lr.drag = 0;
    widget_layout(view); /* any row still sliding, in its place */
    lr_title(page);
}

/* Whether the page is in Edit Library (bar_title_of names it so). */
int lr_editing(void *page) { return page && page == lr.page; }
/* Whether w is a Library row's own child's child in Edit Library: its chevron gives way to the grip
 * (navigation.c paint_into). */
int lr_edit_row(void *w) {
    for (int i = 0; i < 3 && w; ++i) w = P(w, W_PARENT);
    return w && w == lr.view;
}

/* The selected row, shown place id, selected and in view. */
static void lr_select(int id, int n) {
    ringnav_select(lr.view, id, n);
    widget_invalidate_force(lr.page, (void *)0);
}

/* The press the rows had at a hold's end let go (EVT_POINTER_ABORT through the page, as
 * az_index.c's), so none stays pressed or clicks. */
static void lr_abort(void *page) {
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, page, lr.x, lr.y);
    lr.quiet = 1;
    widget_dispatch_event_to_target_recursive(page, abort_event);
    lr.quiet = 0;
}

/* A finger takes row: Edit Library entered if it wasn't, the press the rows had let go, and the
 * row follows the finger from where it was caught. */
static void lr_grab(void *page, void *view, void *row) {
    if (!lr.page) lr_enter(page, view);
    if (lr.page != page) return;
    lr.held = (void *)0;
    stop_timer(&lr.hold);
    lr_abort(page);
    pull_cancel(); /* the pull to search the press began is the drag's now */
    list_stop_scroll(view);
    if (lr.lifted && !lr.drag) lr_save(view); /* a row Centre carried stays where it got to */
    int xy[2] = { lr.x, lr.y };
    widget_to_local(view, xy);
    lr.lifted = row;
    lr.drag = 1;
    lr.grab = xy[1] - I(row, W_Y);
    void *rows[LR_ROWS];
    int n = lr_rows(view, rows);
    prop(view, SEL, lr_index(rows, n, row));
    widget_invalidate_force(page, (void *)0);
}

/* The hold: Centre's enters or leaves Edit Library, a finger's takes its row. */
static int lr_hold_due(const void *info) {
    (void)info;
    lr.hold = 0;
    void *top = window_manager_get_top_window(window_manager()), *view = lr_view_of(top),
         *row = lr.held;
    lr.held = (void *)0;
    if (!view || window_manager_is_animating(window_manager())) return 0;
    if (row) {
        if (top == lr.watch) lr_grab(top, view, row);
        return 0;
    }
    lr.centre = 1;
    if (st.centre_hold) { /* stock's own hold, Shut Down?, never comes */
        timer_remove(st.centre_hold);
        st.centre_hold = 0;
    }
    if (lr.page)
        lr_leave();
    else
        lr_enter(top, view);
    return 0;
}

/* The edge scroll: the list moves on under a still finger, and the row with it. */
static int lr_scroll_due(const void *info) {
    (void)info;
    lr.scroll = 0;
    if (lr.drag) lr_drag();
    return 0;
}

/* The dragged row under the finger: its top where the finger holds it, within the list; it takes
 * the place its middle is over, and near the list's top or bottom the list scrolls on. */
static void lr_drag(void) {
    void *view = lr.view, *row = lr.lifted, *rows[LR_ROWS];
    int n = lr_rows(view, rows), pitch = I(row, W_H), h, y0 = lr_box(view, &h);
    int at = lr.y - y0, off = I(view, SCROLL_Y), end = I(view, VIEW_CONTENT_H) - h;
    int dir = at < LR_EDGE && off > 0 ? -1 : at > h - LR_EDGE && off < end ? 1 : 0;
    if (dir) {
        off += dir * LR_SCROLL_PX;
        scroll_view_set_offset(view, I(view, SCROLL_X), off < 0 ? 0 : off > end ? end : off);
        if (!lr.scroll) lr.scroll = timer_add(lr_scroll_due, (void *)0, LR_SCROLL_MS);
    } else
        stop_timer(&lr.scroll);
    int y = at + I(view, SCROLL_Y) - lr.grab, last = (n - 1) * pitch;
    y = y < 0 ? 0 : y > last ? last : y;
    int b = pitch > 0 ? (y + pitch / 2) / pitch : 0;
    if (lr_move(row, b)) prop(view, SEL, b);
    I(row, W_Y) = y;
    widget_invalidate_force(lr.page, (void *)0);
}

/* The finger lifts: the row slides into its place, and the order is saved. */
static void lr_drop(void) {
    void *row = lr.lifted, *button = widget_count_children(row) ? widget_get_child(row, 0) : 0;
    unsigned abort_event[12];
    stop_timer(&lr.scroll);
    /* a press on the grip reaches the row's button after lr_edit_touch takes it: the button alone
     * lets it go, as an abort through the page glides the view back to where it was pressed */
    if (button)
        stock_dispatch_trampoline(
            button, pointer_event_init(abort_event, EVT_POINTER_ABORT, button, lr.x, lr.y));
    lr.drag = 0;
    lr.lifted = (void *)0;
    prop(row, "_lr_from", I(row, W_Y));
    lr.slide_at = (unsigned)time_now_ms();
    if (!lr.slide) lr.slide = timer_add(lr_slide_step, (void *)0, LR_STEP_MS);
    lr_save(lr.view);
    widget_invalidate_force(lr.page, (void *)0);
}

/* navigation.c's key-down: Centre pressed on the Library page starts its hold. */
void lr_edit_keydown(unsigned key) {
    if (key != KEY_CENTER || lr.hold || lr.centre || lr.drag) return;
    void *wm = window_manager();
    if (!lr_view_of(window_manager_get_top_window(wm)) || window_manager_is_animating(wm)) return;
    lr.held = (void *)0;
    lr.hold = timer_add(lr_hold_due, (void *)0, LR_HOLD_MS);
}

/* navigation.c's key filter, a key's release on top: 1 if Edit Library took it. The release of
 * the Centre hold that entered or left it; in it, Centre lifts or drops the selected row, the
 * wheel carries a lifted row (owned: its press was silenced, so a move clicks), Return puts a
 * carried row back or leaves. */
int lr_edit_key(void *top, unsigned key, int owned) {
    if (key == KEY_CENTER) {
        if (lr.centre) {
            lr.centre = 0;
            *(volatile unsigned char *)KEY_LOCKOUT = 0; /* the wheel at once, as a click's */
            return 1;
        }
        if (!lr.held) stop_timer(&lr.hold);
    }
    if (!lr.page || top != lr.page || lr.drag || !usable()) return 0;
    void *rows[LR_ROWS];
    int n = lr_rows(lr.view, rows), id = widget_get_prop_int(lr.view, SEL, -1);
    if (key == KEY_CENTER) {
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
        st.touch_mode = 0;
        if (lr.lifted) {
            lr.lifted = (void *)0;
            lr_save(lr.view);
        } else if (id >= 0 && id < n) {
            lr.lifted = rows[id];
            lr.from = lr_child(rows[id]);
        } else
            id = 0;
        lr_select(id, n);
        return 1;
    }
    if (key == KEY_RETURN) {
        if (*(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0; /* a held Return's */
        if (!lr.lifted) {
            lr_leave();
            return 1;
        }
        void *row = lr.lifted;
        lr.lifted = (void *)0;
        if (lr_child(row) != lr.from) lr_place(row, lr.from);
        n = lr_rows(lr.view, rows);
        lr_select(lr_index(rows, n, row), n);
        return 1;
    }
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (!dir || !lr.lifted) return 0;
    int a = lr_index(rows, n, lr.lifted);
    if (lr_move(lr.lifted, a + dir)) {
        lr_select(a + dir, n);
        if (owned) buzzeer_switch(1);
    }
    return 1;
}

/* navigation.c's touch hook, a press on top after stock's: on a Library row under OLED it starts
 * the row's hold, and in Edit Library a press on a row's grip takes the row at once. Returns 1 if
 * it took the touch. */
int lr_edit_touch(void *event) {
    void *top = window_manager_get_top_window(window_manager()), *view = lr_view_of(top);
    if (!view || !event) return 0;
    int xy[2] = { lr.x = I(event, EVENT_X), lr.y = I(event, EVENT_Y) }, h;
    lr_box(view, &h);
    widget_to_local(view, xy); /* the view's content's: its scroll counted */
    int y = xy[1];
    if (xy[0] < 0 || xy[0] >= I(view, W_W) || y < I(view, SCROLL_Y) || y >= I(view, SCROLL_Y) + h)
        return 0;
    void *rows[LR_ROWS], *row = (void *)0;
    for (int i = 0, n = lr_rows(view, rows); i < n && !row; ++i)
        if (y >= I(rows[i], W_Y) && y < I(rows[i], W_Y) + I(rows[i], W_H)) row = rows[i];
    if (!row || !lr_bind(top)) return 0;
    if (lr.page == top && lr.x >= 375 - LR_GRIP_ZONE) {
        lr_grab(top, view, row);
        return 1;
    }
    lr.held = row;
    lr.watch = top;
    rearm(&lr.hold, lr_hold_due, LR_HOLD_MS);
    return 0;
}

/* Whether a finger drags a row: navigation.c's touch hook leaves its moves to lr_event. */
int lr_edit_dragging(void) { return lr.drag; }

/* navigation.c's click observer: in Edit Library a tap on a row selects it and opens nothing. */
int lr_edit_click(void *target) {
    if (!lr.view) return 0;
    for (void *w = target; w; w = P(w, W_PARENT))
        if (P(w, W_PARENT) == lr.view) {
            void *rows[LR_ROWS];
            int n = lr_rows(lr.view, rows), id = lr_index(rows, n, w);
            st.touch_mode = 0;
            if (id >= 0) lr_select(id, n);
            return 1;
        }
    return 0;
}

/* The grip, its middle at x, y: three bars in rgb, scaled by pct. */
static void lr_grip(void *canvas, int x, int y, unsigned rgb, int pct) {
    int w = LR_GRIP_W * pct / 100, gap = LR_GRIP_GAP * pct / 100;
    stock_canvas_set_fill_color(canvas, RGBA(rgb));
    for (int k = -1; k <= 1; ++k)
        canvas_fill_rect(canvas, x - w / 2, y + k * gap - 1, w, LR_GRIP_H);
}

/* s at x (its left), its middle at y, in face at px; its width. */
static int lr_text(void *canvas, const char *face, const unsigned *s, int x, int y, unsigned px,
                   unsigned rgb) {
    unsigned n = wide_len(s);
    if (!n) return 0;
    canvas_set_font(canvas, face, px);
    int w = (int)(canvas_measure_text(canvas, s, n) + 0.5f);
    rect_t r = { x, y - (int)px, w + 2, 2 * (int)px };
    final_text(canvas, face, s, &r, px, 0xff000000u | rgb);
    return w;
}

/* ringnav_paint: Edit Library over its page w, after everything in it. Each shown row's grip in
 * the chevron's place (Text Tertiary; On Accent on the selection), the lifted row on its card,
 * and the hint under the rows. */
void lr_edit_paint(void *w, void *canvas) {
    if (!lr.page || w != lr.page || !P(canvas, CANVAS_LCD)) return;
    static const unsigned drag[] = { 'D', 'r', 'a', 'g', 0 },
                          rest[] = { ' ',  't', 'o', ' ', 'm', 'o', 'v', 'e', ' ',
                                     0xb7, ' ', 'C', 'e', 'n', 't', 'r', 'e', ' ',
                                     'p',  'i', 'c', 'k', 's', ' ', 'u', 'p', 0 };
    void *view = lr.view, *rows[LR_ROWS], *lcd = P(canvas, CANVAS_LCD);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int light = ipod_light(), ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y), h,
        box = lr_box(view, &h), top = box - oy - I(view, SCROLL_Y), n = lr_rows(view, rows),
        sel = st.touch_mode ? -1 : widget_get_prop_int(view, SEL, -1),
        gx = 375 - geom_val(V_RIGHT_X) - 5 - ox;
    unsigned ink = light ? 0x000000 : 0xffffff, third = light ? 0x6e6e6e : 0x858585;
    rect_t old, clip; /* clips are the screen's, within the paint's own */
    if (clip_within(canvas, &old.x, &clip.x, 0, box, 375, h)) {
        canvas_set_clip_rect(canvas, &clip);
        for (int i = 0; i < n; ++i)
            if (rows[i] != lr.lifted)
                lr_grip(canvas, gx, top + I(rows[i], W_Y) + I(rows[i], W_H) / 2,
                        i == sel ? 0xffffff : third, 100);
        canvas_set_clip_rect(canvas, &old);
    }
    void *row = lr.lifted;
    if (row) { /* the card: the bar's box (16 px in each side) grown about its middle */
        int cy = top + I(row, W_Y) + I(row, W_H) / 2, cx = (375 - 2 * ox) / 2,
            bw = (375 - 32) * LR_LIFT / 100, bh = I(row, W_H) * LR_LIFT / 100;
        rect_t card = { cx - bw / 2, cy - bh / 2, bw, bh },
               ring = { card.x - 1, card.y - 1, card.w + 2, card.h + 2 };
        /* the list's box grown by a card at each end, so one at either end shows whole */
        if (clip_within(canvas, &old.x, &clip.x, 0, box - bh, 375, h + 2 * bh))
            canvas_set_clip_rect(canvas, &clip);
        for (int k = 3; k > 0; --k) { /* its shadow, 6 px down, softening out */
            rect_t s = { card.x - 2 * k, card.y + 6 - 2 * k, card.w + 4 * k, card.h + 4 * k };
            fill_box(canvas, &s, (RGBA(0) & 0xffffffu) | (unsigned)(0x30 - 0x0c * k) << 24,
                     9 + 2 * k);
        }
        fill_box(canvas, &ring, RGBA(flip_if(light, 0x141414)), 10);
        fill_box(canvas, &card, RGBA(flip_if(light, LR_CARD_RGB)), 9);
        void *button = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
            void *c = widget_get_child(button, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
            int lx = I(button, W_X) + I(c, W_X) - ox; /* the words' left, grown from the middle */
            const unsigned *s = widget_get_text(c);
            if (s)
                lr_text(canvas, "inter-medium", s, cx + (lx - cx) * LR_LIFT / 100, cy, LR_LIFT_PX,
                        ink);
            break;
        }
        lr_grip(canvas, cx + (gx - cx) * LR_LIFT / 100, cy, ink, LR_LIFT);
        canvas_set_clip_rect(canvas, &old);
    }
    canvas_set_font(canvas, "inter", LR_HINT_PX);
    int w1 = (int)(canvas_measure_text(canvas, drag, wide_len(drag)) + 0.5f),
        w2 = (int)(canvas_measure_text(canvas, rest, wide_len(rest)) + 0.5f),
        x = (375 - w1 - 13 - w2) / 2 - ox, y = LR_HINT_Y - oy;
    lr_text(canvas, "inter", drag, x, y, LR_HINT_PX, third); /* in the paint's own clip */
    stock_canvas_set_fill_color(canvas, RGBA(third));        /* the grip as the hint's glyph */
    for (int k = -1; k <= 1; ++k) canvas_fill_rect(canvas, x + w1 + 4, y + 3 * k, 9, 1);
    lr_text(canvas, "inter", rest, x + w1 + 13, y, LR_HINT_PX, third);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}
