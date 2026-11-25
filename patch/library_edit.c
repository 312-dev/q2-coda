/* The Library's and Home's row order and which rows show, and OLED's Edit Library and Edit Home
 * (docs/oled-design.md#edit-library, docs/internals.md#library-order). Each row carries a letter
 * (LR_PROP: home.c ringnav_localmusic for the Library's, lr_home_tag for Home's) that names it in
 * its list's saved order, LR_FILE or LR_HOME_FILE: those letters in the user's order, each led by
 * LR_SHOWN or LR_HIDDEN once the user has shown or hidden it, replaced whole (blob_io: written
 * beside it, synced, renamed over it), so a power cut leaves the old file or the new one.
 * lr_resolve_in reads it against the list's default order (lr_default, lr_home), the one table of
 * it: a letter it doesn't know is dropped, and a row it doesn't name (one a later firmware adds)
 * goes in at its default place, after the row before it there.
 *
 * A row shows or not by its mark: none (Auto) leaves it to the row's own rule, its LR_AUTO (the
 * menu review's hidden Library rows, Home's Folders only while the card has folders), LR_SHOWN or
 * LR_HIDDEN is the user's and sticks. A row with no LR_AUTO (stock's My Fav and Frequent, which
 * Ratings and Most Played replace) is neither shown nor moved; Home's Settings is LR_LOCKED, always
 * shown, so Home always has a way back.
 *
 * Under OLED, Center held LR_HOLD_MS on the Library page or Home's main list, or a finger held as
 * long on a row, enters the editor: the bar's hidden title reads "Edit Library" or "Edit Home",
 * every row the user can show shows (a hidden one dimmed to Text Tertiary), each with a check
 * circle at its left, its words moved right of it, each row's chevron gives way to a grip, and a
 * hint shows under the rows: the list ends LR_ROOM higher, and its rows past that end and above its
 * top fade as every list's do, into the hint's band (lr_edit_room, lr_edit_foot). Home is laid out
 * as Full while it is open (theme_engine.c ipod_home). A finger on a row's grip (the last
 * LR_GRIP_ZONE px) drags it at once, a finger held on a row drags it after LR_HOLD_MS, the row held
 * inside the list's box; the rows it passes slide aside over LR_SLIDE_MS (at once under Reduce
 * Motion), the list scrolls while the finger is within LR_EDGE px of its top or bottom, and the
 * lift puts the row in its place and saves. Center lifts the selected row (on a raised card in the
 * selection bar's box, its words in Inter Medium, nothing of it moved), the wheel carries it,
 * Center puts it down and saves; Return while it is carried puts it back where it was. Play/Pause,
 * or a tap on a row's circle (its first LR_VIS_ZONE px), shows or hides the row. Return, or Center
 * held again, saves and leaves; the rows hidden then leave the list. A row the editor doesn't show
 * keeps its place: the shown rows' new order fills the places they held (lr_merge). */
#include "navigation.h"

#define LR_FILE "/mnt/data/ringnav-library-order"
#define LR_HOME_FILE "/mnt/data/ringnav-home-order"
#define LR_PROP "_lr_code"
#define LR_AUTO "_lr_auto" /* the row's own rule: LR_A_* */
#define LR_SHOWN '+'       /* in the saved order, before a row's letter: the user showed it */
#define LR_HIDDEN '-'      /* ... hid it */
#define LR_MAX 64       /* the saved order's bytes, its letters and marks and a NUL: room for all */
#define LR_SLIDE_MS 150 /* the rows a moved row passes slide aside over this */
#define LR_STEP_MS 16   /* ... a frame at a time */
#define LR_SLOP 8       /* a finger that moves this far before its hold is a scroll */
#define LR_GRIP_ZONE 64 /* a press this near the screen's right edge is on the grip */
#define LR_VIS_ZONE 52  /* a tap this near the screen's left edge is on the circle */
#define LR_EDGE 16      /* a dragged row this near the list's top or bottom scrolls it */
#define LR_SCROLL_PX 8  /* ... this far */
#define LR_SCROLL_MS 40 /* ... this often */
#define LR_GRIP_X 5     /* the grip (list_marks.c grip_mark): its middle this far left of the */
                        /* right column, where the chevron's is */
#define LR_CIRCLE_X 34  /* the check circle (list_marks.c check_circle): its middle at this x */
#define LR_TEXT_X 52    /* the row's words, right of it */
#define LR_LABEL_PAD 2  /* their label's own margin before them (tools/ipod.py NP_GRID_MARGIN) */
#define LR_LIFT 108     /* the lifted row's card: the selection bar's box, 108% of its height */
#define LR_CARD_X 16    /* ... its sides, the bar's inset (docs/oled-design.md#selection-bar) */
#define LR_HINT_PX TYPE(13) /* the hint: Inter Regular 13 px (AWTK's line size, 13 x 1.21) */
#define LR_HINT_Y 298       /* its middle, 14 px above the screen's foot */
#define LR_LIFT_PX TYPE(20) /* the lifted row's words: Inter Medium at the row's 20 px */
#define LR_ROOM 32  /* the editor's list ends a row higher than the set's, the hint below it */
#define LR_FOOT 284 /* ... and the rows past that end fade into the ground by this y, 34 px */
                    /* below it, as a list's do 34 px below the set's (home.c list_fades) */

enum { LR_A_NONE, LR_A_SHOWN, LR_A_HIDDEN, LR_A_LOCKED }; /* LR_AUTO */
enum { LR_LIBRARY, LR_HOME };

/* The Library's rows in their default order (the menu review's, 2026-11-15), each its letter in
 * LR_FILE: Shuffle (hidden), Songs, Playlists, Artists, Albums, Genres, Years, Ratings, My Fav
 * (hidden), Recently Added, Recent, Most Played, Frequent (hidden), Hi-Res (hidden), Long Songs
 * (hidden), Podcasts, Audiobooks, Videos, Upload Scrobbles (hidden), Update Local Music (never
 * listed: Settings > General > Storage has it, storage.c). A letter is never reused: a new row
 * takes a new one, here where it goes by default. o named Photos, B Books, S Search and G Games,
 * all gone (2026-11-19: each list page has its own search bar, search_bar.c, and Games is Home's):
 * a saved order holding them loads without them (lr_resolve). */
const char lr_default[] = "hspalgerfncmqiLPAvUu";
_Static_assert(sizeof lr_default <= LR_ROWS, "a row per letter");
/* Home's main list's rows (tools/ipod.py HOME_ROWS) in their default order, each its letter in
 * LR_HOME_FILE, by its row's name: Now Playing, Library, Radio, Games, Folders, Cover Flow,
 * Search, Settings. The same rule: a new row takes a new letter. s named Streaming, which Settings
 * has since 2026-11-19: a saved order holding it loads without it, and a new row (Games, then Radio
 * Tuner, t) goes in after the row before it in this order (lr_resolve), so an older order keeps its
 * rows and gains the new one in its default place. */
const char lr_home[] = "pltgfcqS";
static const char *const lr_home_names[] = { "btn_playing", "btn_localmusic", "btn_radio", "btn_games",
                                             "btn_folder",  "btn_coverflow",  "btn_home_search",
                                             "btn_settings" };
_Static_assert(sizeof lr_home - 1 == sizeof lr_home_names / sizeof *lr_home_names, "a name each");

static const struct {
    const char *def, *file, *tmp, *title;
} lr_lists[] = { { lr_default, LR_FILE, LR_FILE ".tmp", "Edit Library" },
                 { lr_home, LR_HOME_FILE, LR_HOME_FILE ".tmp", "Edit Home" } };

static struct {
    void *page, *view;    /* the page in its editor and its rows' view, 0 when not */
    void *lifted;         /* the row Center lifted or a finger drags */
    void *held;           /* the row a finger holds, LR_HOLD_MS not yet up */
    void *watch;          /* ... and its page */
    unsigned hold;        /* Center's or that finger's hold timer */
    unsigned slide;       /* the rows' slide, a frame at a time */
    unsigned scroll;      /* a dragged row's scroll at the list's edge */
    unsigned slide_at;    /* the slide's start, time_now_ms */
    int from;             /* the lifted row's place in the view when Center lifted it */
    int x, y;             /* the finger, on screen */
    int grab;             /* the finger's distance below the dragged row's top */
    int list;             /* the editor's list: LR_LIBRARY or LR_HOME */
    char vis[LR_ROWS];    /* ... and its rows' marks, by their place in its default order */
    unsigned char centre; /* Center's hold went off: its release is taken */
    unsigned char drag;   /* a finger drags lifted */
    unsigned char quiet;  /* the abort lr_grab sends is its own */
} lr __attribute__((section(".scratch")));

/* Letter c's place in the default order def, -1 if it names no row. */
static int lr_at_in(const char *def, int c) {
    for (int i = 0; def[i]; ++i)
        if (def[i] == c) return i;
    return -1;
}
int lr_at(int c) { return lr_at_in(lr_default, c); }

static int has(const char *s, int n, int c) {
    for (int i = 0; i < n; ++i)
        if (s[i] == c) return i;
    return -1;
}

/* The saved order (NUL-ended) as the rows go: its known letters, each once, then every row it
 * doesn't name at its default place in def, after the nearest row before it there (first, if
 * none). out holds LR_MAX; vis, if given, LR_ROWS: each row's mark by its place in def, 0 for none.
 * Returns out's length. */
int lr_resolve_in(const char *def, const char *saved, char *out, char *vis) {
    int n = 0, mark = 0;
    if (vis) memset(vis, 0, LR_ROWS);
    for (int i = 0; saved[i] && i < LR_MAX - 1; ++i) {
        int c = saved[i], at = lr_at_in(def, c);
        if (c == LR_SHOWN || c == LR_HIDDEN) {
            mark = c;
            continue;
        }
        if (at >= 0 && has(out, n, c) < 0) {
            out[n++] = (char)c;
            if (vis) vis[at] = (char)mark;
        }
        mark = 0;
    }
    for (int i = 0; def[i]; ++i) {
        if (has(out, n, def[i]) >= 0) continue;
        int at = 0;
        for (int j = i; j-- > 0;) {
            int k = has(out, n, def[j]);
            if (k >= 0) {
                at = k + 1;
                break;
            }
        }
        for (int k = n++; k > at; --k) out[k] = out[k - 1];
        out[at] = def[i];
    }
    out[n] = 0;
    return n;
}
int lr_resolve(const char *saved, char *out) {
    return lr_resolve_in(lr_default, saved, out, (char *)0);
}

/* full with the shown rows in their new order (shown, NUL-ended): the places shown's rows held
 * take them in turn, and every other row (hidden, or not on this page) keeps its own. */
void lr_merge(char *full, const char *shown) {
    int n = (int)strlen(shown);
    for (int i = 0, k = 0; full[i] && k < n; ++i)
        if (has(shown, n, full[i]) >= 0) full[i] = shown[k++];
}

/* The order list's rows go in, its file's resolved, and their marks. */
static void lr_load(int list, char *out, char *vis) {
    char saved[LR_MAX] = { 0 };
    blob_io(lr_lists[list].file, lr_lists[list].tmp, saved, sizeof saved, 0);
    saved[LR_MAX - 1] = 0;
    lr_resolve_in(lr_lists[list].def, saved, out, vis);
}

/* order (resolved) with each row's mark before it, as list's file. */
static void lr_store(int list, const char *order, const char *vis) {
    char out[LR_MAX] = { 0 };
    for (int i = 0, n = 0; order[i] && n < LR_MAX - 2; ++i) {
        int at = lr_at_in(lr_lists[list].def, order[i]);
        if (at >= 0 && vis[at]) out[n++] = vis[at];
        out[n++] = order[i];
    }
    blob_io(lr_lists[list].file, lr_lists[list].tmp, out, sizeof out, 1);
}

/* Whether a row with mark (0, LR_SHOWN, LR_HIDDEN) and rule rule (LR_A_*) shows. */
int lr_shows(int mark, int rule) {
    if (rule == LR_A_LOCKED) return 1;
    return mark ? mark == LR_SHOWN : rule == LR_A_SHOWN;
}

/* row's mark in list's marks vis. */
static int lr_mark(int list, const char *vis, void *row) {
    int at = lr_at_in(lr_lists[list].def, widget_get_prop_int(row, LR_PROP, 0));
    return at >= 0 ? vis[at] : 0;
}

/* ringnav_localmusic's row for letter c, tagged, shown by its own rule (lr_auto_hide, lr_retire
 * change it). */
void lr_put(void **rows, int c, void *row) {
    int at = lr_at(c);
    if (at < 0 || !row) return;
    rows[at] = row;
    prop(row, LR_PROP, c);
    prop(row, LR_AUTO, LR_A_SHOWN);
}
/* A Library row hidden unless the user shows it (the menu review's). */
void lr_auto_hide(void *row) {
    if (row) prop(row, LR_AUTO, LR_A_HIDDEN);
}
/* A Library row never shown and not in Edit Library: its place kept, as stock's My Fav. */
void lr_retire(void *row) {
    if (!row) return;
    prop(row, LR_AUTO, LR_A_NONE);
    widget_set_visible(row, 0, 0);
}

/* The view's rows, by their letters, in the saved order, each shown as its mark and rule say. */
void lr_apply(void **rows) {
    char order[LR_MAX], vis[LR_ROWS];
    lr_load(LR_LIBRARY, order, vis);
    for (unsigned i = 0, n = 0; order[i]; ++i) {
        void *row = rows[lr_at(order[i])];
        if (!row) continue;
        widget_restack(row, n++);
        int rule = widget_get_prop_int(row, LR_AUTO, LR_A_NONE);
        if (rule != LR_A_NONE) widget_set_visible(row, lr_shows(vis[lr_at(order[i])], rule), 0);
    }
}

/* Home's main list's rows tagged by their names (coverflow.c coverflow_home): Settings locked
 * shown, the rest shown by their own rule until lr_home_apply says. */
void lr_home_tag(void *view) {
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *row = widget_get_child(view, i);
        const char *name = widget_get_prop_str(row, "name", "");
        for (unsigned k = 0; k < sizeof lr_home - 1; ++k)
            if (!tk_strcmp(name, lr_home_names[k])) {
                prop(row, LR_PROP, lr_home[k]);
                prop(row, LR_AUTO, lr_home[k] == 'S' ? LR_A_LOCKED : LR_A_SHOWN);
            }
    }
}

/* Whether the editor is open on Home: theme_engine.c ipod_home lays Home out as Full meanwhile. */
int lr_home_editing(void) { return lr.page && lr.list == LR_HOME; }

/* The rows view shows before row, in its order. */
static int lr_shown_before(void *view, void *row) {
    int k = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        if (c == row) break;
        k += widget_get_visible(c) != 0;
    }
    return k;
}

/* coverflow.c coverflow_home_layout, before the list is laid out again: Home's rows in the saved
 * order, each shown as its mark and rule say, Now Playing only while something is loaded (np) or a
 * guest plays (guest.c guest_active), its words its state's (home_np.c home_np_row), and
 * Folders by its own rule only while the card has folders (home.c home_folders); in Edit Home every
 * row it lists. A row that comes or goes keeps the selection and the rows in view
 * (home.c home_keep); the hidden rows go last, as the list's layout keeps a hidden row's slot. */
void lr_home_apply(void *view, int np) {
    if (!view) return;
    char order[LR_MAX], vis[LR_ROWS];
    lr_load(LR_HOME, order, vis);
    int editing = lr.page && lr.view == view, folders = home_folders();
    if (editing) memcpy(vis, lr.vis, sizeof vis);
    void *rows[LR_ROWS];
    int n = 0;
    for (int i = 0; order[i]; ++i)
        for (unsigned j = 0, k = widget_count_children(view); j < k; ++j) {
            void *row = widget_get_child(view, j);
            if (widget_get_prop_int(row, LR_PROP, 0) == order[i] && n < LR_ROWS) {
                widget_restack(row, (unsigned)n);
                rows[n++] = row;
                break;
            }
        }
    for (int i = 0; i < n; ++i) {
        void *row = rows[i];
        int c = widget_get_prop_int(row, LR_PROP, 0), rule = widget_get_prop_int(row, LR_AUTO, 0);
        if (c == 'f' && rule != LR_A_LOCKED) rule = folders ? LR_A_SHOWN : LR_A_HIDDEN;
        if (rule != LR_A_LOCKED) prop(row, LR_AUTO, rule); /* the editor's circle reads it */
        if (c == 'p') home_np_row(row, editing); /* home_np.c: Playing, Paused or Guest Play */
        int on = editing ||
                 (lr_shows(vis[lr_at_in(lr_home, c)], rule) && (c != 'p' || np || guest_active()));
        if (on == !!widget_get_visible(row)) continue;
        int k = lr_shown_before(view, row);
        widget_set_visible(row, on, 0);
        widget_set_sensitive(row, on);
        home_keep(on ? 1 : -1, k);
    }
    for (int i = 0; i < n; ++i) /* the hidden ones last, in their order */
        if (!widget_get_visible(rows[i])) widget_restack(rows[i], widget_count_children(view) - 1);
}

/* The shown rows' letters in view's order, saved over the places they held, with the editor's
 * marks. */
static void lr_save(void *view) {
    char full[LR_MAX] = { 0 }, shown[LR_MAX], vis[LR_ROWS];
    int n = 0;
    for (unsigned i = 0, k = widget_count_children(view); i < k && n < LR_MAX - 1; ++i) {
        void *row = widget_get_child(view, i);
        int c = widget_get_prop_int(row, LR_PROP, 0);
        if (c && widget_get_visible(row)) shown[n++] = (char)c;
    }
    shown[n] = 0;
    lr_load(lr.list, full, vis);
    lr_merge(full, shown);
    lr_store(lr.list, full, lr.vis);
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

/* The rows' view of the page top when it can be edited and takes input, else 0; *list its list.
 * The Library page's, or Home's main list's (coverflow.c coverflow_home_edit_view). */
static void *lr_view_of(void *top, int *list) {
    if (!top || !usable()) return (void *)0;
    if (!tk_strcmp(widget_get_prop_str(top, "name", ""), "localmusic_page")) {
        *list = LR_LIBRARY;
        return widget_lookup(top, "scroll_view_localmusic", 1);
    }
    *list = LR_HOME;
    return coverflow_home_edit_view(top);
}

/* The view's top on screen and the height its rows show in: to the set's list end on the screen
 * (list_fit_end), where a selected row stops (docs/oled-design.md#corners), LR_ROOM higher in the
 * editor, as navigation.c sel_height reads it. */
static int lr_box(void *view, int *h) {
    int y = 0, end = geom_val(V_LIST_END) ? list_fit_end() - lr_edit_room(view) : 0;
    for (void *p = view, *wm = window_manager(); p && p != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    *h = end && end - y < I(view, W_H) ? end - y : I(view, W_H);
    return y;
}

/* w's x on screen. */
static int lr_screen_x(void *w) {
    int x = 0;
    for (void *wm = window_manager(); w && w != wm; w = P(w, W_PARENT)) x += I(w, W_X);
    return x;
}

/* A row's words: its first hscroll_label, the row's own (Home's) or its button's (the Library's).
 */
static void *lr_label(void *row) {
    for (unsigned i = 0, n = widget_count_children(row); i < n; ++i) {
        void *c = widget_get_child(row, i);
        if (!tk_strcmp(widget_get_type(c), "hscroll_label")) return c;
        for (unsigned j = 0, k = widget_count_children(c); j < k; ++j)
            if (!tk_strcmp(widget_get_type(widget_get_child(c, j)), "hscroll_label"))
                return widget_get_child(c, j);
    }
    return (void *)0;
}

/* In the editor (on) each row's words start at LR_TEXT_X on screen, right of its circle, their
 * right end kept; out of it (0) back where they were. Again after Home lays its rows out. */
#define LR_LABEL_X "_lr_lx"
#define LR_LABEL_W "_lr_lw"
void lr_edit_labels(void *view, int on) {
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *row = widget_get_child(view, i), *l = lr_label(row);
        if (!l || !widget_get_prop_int(row, LR_AUTO, LR_A_NONE)) continue;
        int dx = LR_TEXT_X - lr_screen_x(l);
        if (on && dx) {
            prop(l, LR_LABEL_X, I(l, W_X) + 1);
            prop(l, LR_LABEL_W, I(l, W_W));
            widget_move_resize(l, I(l, W_X) + dx, I(l, W_Y), I(l, W_W) - dx, I(l, W_H));
        } else if (!on && widget_get_prop_int(l, LR_LABEL_X, 0)) {
            widget_move_resize(l, widget_get_prop_int(l, LR_LABEL_X, 1) - 1, I(l, W_Y),
                               widget_get_prop_int(l, LR_LABEL_W, I(l, W_W)), I(l, W_H));
            prop(l, LR_LABEL_X, 0);
        }
    }
}

/* One frame of the slide: each row from where it showed towards its place, cubic out. */
static int lr_slide_step(const void *info) {
    (void)info;
    if (!lr.view) return lr.slide = 0;
    unsigned t = (unsigned)time_now_ms() - lr.slide_at,
             ms = (unsigned)motion_ms(LR_SLIDE_MS, MO_SPATIAL);
    int left = t >= ms ? 0 : 1000 - (int)(t * 1000 / ms);
    int ease = motion_ease(1000 - left, EASE_CUBIC_OUT) * 1024 / 1000;
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

/* The bar's hidden title follows the page in or out of its editor. */
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

/* The selected row, shown place id, selected and in view. */
static void lr_select(int id, int n) {
    ringnav_select(lr.view, id, n);
    widget_invalidate_force(lr.page, (void *)0);
}

/* Once rows came or went: the row selected among was (the n rows shown before) selected again,
 * or if it went the first still shown after it, else the last before it. */
static void lr_reselect(void *view, void **was, int n, int id) {
    void *rows[LR_ROWS], *row = (void *)0;
    for (int i = id; i >= 0 && i < n && !row; ++i)
        if (widget_get_visible(was[i])) row = was[i];
    for (int i = id < n ? id : n; i-- > 0 && !row;)
        if (widget_get_visible(was[i])) row = was[i];
    int k = lr_rows(view, rows), at = row ? lr_index(rows, k, row) : -1;
    if (at >= 0) ringnav_select(view, at, k);
}

/* The Library's rows shown as their marks and rules say, or (all) every row Edit Library lists. */
static void lr_library_show(void *view, const char *vis, int all) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *row = widget_get_child(view, i);
        int rule = widget_get_prop_int(row, LR_AUTO, LR_A_NONE);
        if (rule != LR_A_NONE && widget_get_prop_int(row, LR_PROP, 0))
            widget_set_visible(row, all || lr_shows(lr_mark(LR_LIBRARY, vis, row), rule), 0);
    }
}

/* The page's rows as they show now: every row the editor lists while it is open, else the shown
 * ones; the selection kept on its row. */
static void lr_show(void *view, int all) {
    void *was[LR_ROWS];
    int n = lr_rows(view, was), id = widget_get_prop_int(view, SEL, -1);
    if (lr.list == LR_HOME) {
        coverflow_home_layout();          /* lr_home_apply, lr_edit_labels */
        widget_layout(P(view, W_PARENT)); /* the rows in place now, for a finger's grab */
    } else {
        lr_library_show(view, lr.vis, all);
        widget_layout(view);
        lr_edit_labels(view, all);
    }
    lr_reselect(view, was, n, id);
}

static void lr_enter(void *page, void *view, int list) {
    if (!lr_bind(page)) return;
    char order[LR_MAX];
    lr.list = list;
    lr_load(list, order, lr.vis);
    lr.page = page, lr.view = view;
    lr_show(view, 1);
    lr_title(page);
}

/* Out of the editor, the order and marks saved; a row still carried stays where it is, and the
 * rows hidden leave the list. */
static void lr_leave(void) {
    void *page = lr.page, *view = lr.view;
    lr_save(view);
    stop_timer(&lr.slide);
    stop_timer(&lr.scroll);
    lr.page = lr.view = lr.lifted = (void *)0;
    lr.drag = 0;
    widget_layout(view); /* any row still sliding, in its place */
    lr_show(view, 0);
    lr_title(page);
}

/* Whether the page is in its editor (bar_title_of names it so), and the editor's title. */
int lr_editing(void *page) { return page && page == lr.page; }
const char *lr_edit_title(void) { return lr_lists[lr.list].title; }
/* Whether w is a Library row's own child's child in Edit Library: its chevron gives way to the grip
 * (navigation.c paint_into). */
int lr_edit_row(void *w) {
    for (int i = 0; i < 3 && w; ++i) w = P(w, W_PARENT);
    return w && w == lr.view;
}
/* Whether view is the list in an editor: Home's rows' chevrons give way to grips
 * (navigation.c paint_chevrons). */
int lr_edit_list(void *view) { return view && view == lr.view; }
/* How much higher view's list ends than the set's list end: LR_ROOM in an editor, which keeps the
 * row's room above its hint, else 0 (navigation.c sel_height, row_paint.c list_fade, home.c
 * home_rest). And where the rows past that end have faded into the ground (list_fade,
 * home_fades): in an editor LR_FOOT, the hint's band's top; elsewhere 0, the screen's foot's. */
int lr_edit_room(void *view) { return view && view == lr.view ? LR_ROOM : 0; }
int lr_edit_foot(void *view) { return view && view == lr.view ? LR_FOOT : 0; }

/* The press the rows had at a hold's end let go (EVT_POINTER_ABORT through the page, as
 * az_index.c's), so none stays pressed or clicks. */
static void lr_abort(void *page) {
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, page, lr.x, lr.y);
    lr.quiet = 1;
    widget_dispatch_event_to_target_recursive(page, abort_event);
    lr.quiet = 0;
}

/* A finger takes row: the editor entered if it wasn't, the press the rows had let go, and the
 * row follows the finger from where it was caught. */
static void lr_grab(void *page, void *view, void *row, int list) {
    if (!lr.page) lr_enter(page, view, list);
    if (lr.page != page) return;
    lr.held = (void *)0;
    stop_timer(&lr.hold);
    lr_abort(page);
    pull_cancel(); /* the pull to search the press began is the drag's now */
    list_stop_scroll(view);
    if (lr.lifted && !lr.drag) lr_save(view); /* a row Center carried stays where it got to */
    /* the finger's offset from the row where the editor shows it (the rows it lists and Home's Full
     * layout may have moved it from under the finger): it stays in its place until the finger moves
     */
    int xy[2] = { lr.x, lr.y };
    widget_to_local(view, xy);
    lr.grab = xy[1] - I(row, W_Y);
    lr.lifted = row;
    lr.drag = 1;
    void *rows[LR_ROWS];
    int n = lr_rows(view, rows);
    prop(view, SEL, lr_index(rows, n, row));
    widget_invalidate_force(page, (void *)0);
}

/* The hold: Center's enters or leaves the editor, a finger's takes its row. */
static int lr_hold_due(const void *info) {
    (void)info;
    lr.hold = 0;
    int list = LR_LIBRARY;
    void *top = window_manager_get_top_window(window_manager()), *view = lr_view_of(top, &list),
         *row = lr.held;
    lr.held = (void *)0;
    if (!view || window_manager_is_animating(window_manager())) return 0;
    if (row) {
        if (top == lr.watch) lr_grab(top, view, row, list);
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
        lr_enter(top, view, list);
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
    /* the row's top in the content: under the finger, but never past the list's box on the screen
     * (its top, or the list end less a row), nor past the first row's place or the last's */
    int lo = I(view, SCROLL_Y), hi = lo + h - pitch, y = at + lo - lr.grab, last = (n - 1) * pitch;
    y = y < lo ? lo : y > hi ? hi : y;
    y = y < 0 ? 0 : y > last ? last : y;
    int b = pitch > 0 ? (y + pitch / 2) / pitch : 0;
    if (lr_move(row, b)) prop(view, SEL, b);
    I(row, W_Y) = y;
    widget_invalidate_force(lr.page, (void *)0);
}

/* The finger lifts: the row slides into its place, and the order is saved. */
static void lr_drop(void) {
    unsigned n = widget_count_children(lr.lifted);
    void *row = lr.lifted, *button = n ? widget_get_child(row, n - 1) : 0;
    unsigned abort_event[12];
    stop_timer(&lr.scroll);
    /* a press on the grip reaches the row's button (Home's: its tap image, its last child) after
     * lr_edit_touch takes it: the button alone lets it go, as an abort through the page glides
     * the view back to where it was pressed */
    if (button)
        stock_dispatch_trampoline(
            button, pointer_event_init(abort_event, EVT_POINTER_ABORT, button, lr.x, lr.y));
    lr.drag = 0;
    lr.lifted = (void *)0;
    st.touch_mode = 0; /* the dropped row stays selected, its circle and grip as the selection's */
    prop(row, "_lr_from", I(row, W_Y));
    lr.slide_at = (unsigned)time_now_ms();
    if (!lr.slide) lr.slide = timer_add(lr_slide_step, (void *)0, LR_STEP_MS);
    lr_save(lr.view);
    widget_invalidate_force(lr.page, (void *)0);
}

/* row shown or hidden, the other way from how it shows now: the user's from here on, saved. The
 * locked row stays. */
static void lr_toggle(void *row) {
    int at = row ? lr_at_in(lr_lists[lr.list].def, widget_get_prop_int(row, LR_PROP, 0)) : -1,
        rule = row ? widget_get_prop_int(row, LR_AUTO, LR_A_NONE) : LR_A_NONE;
    if (at < 0 || rule == LR_A_NONE || rule == LR_A_LOCKED) return;
    lr.vis[at] = lr_shows(lr.vis[at], rule) ? LR_HIDDEN : LR_SHOWN;
    lr_save(lr.view);
    widget_invalidate_force(lr.page, (void *)0);
}

/* navigation.c's key-down: Center pressed on the Library page or Home's list starts its hold. */
void lr_edit_keydown(unsigned key) {
    if (key != KEY_CENTER || lr.hold || lr.centre || lr.drag) return;
    void *wm = window_manager();
    int list;
    if (!lr_view_of(window_manager_get_top_window(wm), &list) || window_manager_is_animating(wm))
        return;
    lr.held = (void *)0;
    lr.hold = timer_add(lr_hold_due, (void *)0, LR_HOLD_MS);
}

/* navigation.c's key filter, a key's release on top: 1 if the editor took it. The release of
 * the Center hold that entered or left it; in it, Center lifts or drops the selected row, the
 * wheel carries a lifted row (owned: its press was silenced, so a move clicks), Return puts a
 * carried row back or leaves, Play/Pause (not held) shows or hides the selected row. */
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
    if (key == KEY_PLAY) {
        if (!play_hold_released() && id >= 0 && id < n) lr_toggle(rows[id]);
        return 1;
    }
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

/* navigation.c's touch hook, a press on top after stock's: on a Library or Home row under OLED it
 * starts the row's hold, and in the editor a press on a row's grip takes the row at once and one
 * on its circle waits for its tap (lr_edit_click). Returns 1 if it took the touch. */
int lr_edit_touch(void *event) {
    int list;
    void *top = window_manager_get_top_window(window_manager()), *view = lr_view_of(top, &list);
    if (!view || !event) return 0;
    int xy[2] = { lr.x = I(event, EVENT_X), lr.y = I(event, EVENT_Y) }, h;
    /* a press above the window (Split's raised rows, which the status bar paints) is the bar's: its
     * lift never reaches the page (lr_event), so it starts no hold */
    if (lr.y < I(top, W_Y)) return 0;
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
        lr_grab(top, view, row, list);
        return 1;
    }
    if (lr.page == top && lr.x < LR_VIS_ZONE) return 0;
    lr.held = row;
    lr.watch = top;
    rearm(&lr.hold, lr_hold_due, LR_HOLD_MS);
    return 0;
}

/* Whether a finger drags a row: navigation.c's touch hook leaves its moves to lr_event. */
int lr_edit_dragging(void) { return lr.drag; }

/* navigation.c's click observer: in the editor a tap on a row selects it and opens nothing, and
 * one on its circle shows or hides it. */
int lr_edit_click(void *target) {
    if (!lr.view) return 0;
    for (void *w = target; w; w = P(w, W_PARENT))
        if (P(w, W_PARENT) == lr.view) {
            void *rows[LR_ROWS];
            int n = lr_rows(lr.view, rows), id = lr_index(rows, n, w);
            st.touch_mode = 0;
            if (id >= 0) lr_select(id, n);
            if (id >= 0 && lr.x < LR_VIS_ZONE) lr_toggle(w);
            return 1;
        }
    return 0;
}

/* Whether the painted text belongs to a row the editor shows dimmed: one that is hidden and not
 * selected (navigation.c ringnav_style_color: Text Tertiary). */
int lr_edit_dim(void *w) {
    if (!lr.view || !w) return 0;
    for (; w && P(w, W_PARENT) != lr.view; w = P(w, W_PARENT))
        if (w == lr.page) return 0;
    if (!w || painting_in(st.sel_accent) || w == lr.lifted) return 0;
    return !lr_shows(lr_mark(lr.list, lr.vis, w), widget_get_prop_int(w, LR_AUTO, LR_A_NONE));
}

/* row's check circle's state: shown, hidden, or locked shown. */
static int lr_circle(void *row) {
    int rule = widget_get_prop_int(row, LR_AUTO, LR_A_NONE);
    return rule == LR_A_LOCKED                             ? CIRCLE_LOCKED
           : lr_shows(lr_mark(lr.list, lr.vis, row), rule) ? CIRCLE_ON
                                                           : CIRCLE_OFF;
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

/* ringnav_paint: the editor's marks and its hint and lifted card. On its rows' view w, before the
 * list's own fades (row_paint.c list_fade, home.c home_fades) go over it: each shown row's circle
 * at its left and grip in the chevron's place (Text Tertiary; On Accent on the selection), in the
 * view's box, so the rows past the list end and a scrolled list's top row fade with their marks. On
 * its page w, after everything in it: the hint's band and the hint, and the lifted row on its card
 * over all of it, its circle, words and grip where the row's own stand, so a lift moves nothing. */
void lr_edit_paint(void *w, void *canvas) {
    if (!lr.page || (w != lr.page && w != lr.view) || !P(canvas, CANVAS_LCD)) return;
    static const unsigned drag[] = { 'D', 'r', 'a', 'g', 0 },
                          rest[] = { ' ', 't', 'o', ' ', 'm', 'o', 'v', 'e', ' ', 0xb7,
                                     ' ', 'P', 'l', 'a', 'y', '/', 'P', 'a', 'u', 's',
                                     'e', ' ', 's', 'h', 'o', 'w', 's', ' ', 'o', 'r',
                                     ' ', 'h', 'i', 'd', 'e', 's', 0 };
    void *view = lr.view, *rows[LR_ROWS], *lcd = P(canvas, CANVAS_LCD);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), ground = ui_ink(RGBA(0)), raw = ui_ink_raw(1);
    int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y), h, box = lr_box(view, &h),
        top = box - oy - I(view, SCROLL_Y), n = lr_rows(view, rows),
        sel = st.touch_mode ? -1 : widget_get_prop_int(view, SEL, -1),
        gx = 375 - geom_val(V_RIGHT_X) - LR_GRIP_X - ox, cx0 = LR_CIRCLE_X - ox;
    unsigned ink = tok(TOK_PRIMARY), third = tok(TOK_TERTIARY);
    rect_t old, clip; /* clips are the screen's, within the paint's own */
    if (w == view) {
        if (clip_within(canvas, &old.x, &clip.x, 0, box, 375, I(view, W_H))) {
            canvas_set_clip_rect(canvas, &clip);
            for (int i = 0; i < n; ++i)
                if (rows[i] != lr.lifted) {
                    int y = top + I(rows[i], W_Y) + I(rows[i], W_H) / 2;
                    grip_mark(canvas, gx, y, i == sel ? 0xffffff : third, 100);
                    check_circle(w, canvas, cx0, y, lr_circle(rows[i]), i == sel);
                }
            canvas_set_clip_rect(canvas, &old);
        }
        ui_ink_raw(raw);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    /* the hint's band: the ground from where the rows past the list end have faded into it */
    stock_canvas_set_fill_color(canvas, ground);
    canvas_fill_rect(canvas, -ox, LR_FOOT - oy, 375, 320 - LR_FOOT);
    void *row = lr.lifted;
    if (row) { /* the card: the selection bar's box, LR_LIFT tall */
        int cy = top + I(row, W_Y) + I(row, W_H) / 2, bh = I(row, W_H) * LR_LIFT / 100;
        rect_t card = { LR_CARD_X - ox, cy - bh / 2, 375 - 2 * LR_CARD_X, bh };
        lift_card_paint(canvas, &card);
        void *label = lr_label(row);
        const unsigned *s = label ? widget_get_text(label) : (const unsigned *)0;
        if (s) /* lr_text's left is 1 px short of where its words start */
            lr_text(canvas, "inter-medium", s, lr_screen_x(label) + LR_LABEL_PAD - 1 - ox, cy,
                    LR_LIFT_PX, ink);
        grip_mark(canvas, gx, cy, ink, 100);
        check_circle(w, canvas, cx0, cy, lr_circle(row), 1);
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
