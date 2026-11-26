/* A playlist's songs and On-The-Go under OLED (docs/oled-design.md#playlists,
 * docs/internals.md#on-the-go): a page of its own, opened from a row of Playlists. Action rows
 * first (Shuffle, Add Songs and, for On-The-Go, Save as Playlist and Clear), each its accent glyph
 * before an accent label, then the songs (in the playlist's Sort order, playlist_songs.c, and
 * On-The-Go's as added), each a title with its artist on the right column in Text Tertiary.
 * Centre on a song plays the list from it, as stock plays a playlist's own list (class 0xff13 with
 * its cd_num, so Now Playing's Delete takes it out of this list, not the library); a hold on a song
 * opens its whole menu (Play Next, Play Last, Add to Playlist, Go to Album and Artist), Remove from
 * Playlist (or On-The-Go) last, which the confirmation card asks.
 *
 * On-The-Go's songs are playlist entries under cd_num ON_THE_GO with no header row, so stock's
 * own add (batch_add_file), read (getMusicByPlayList) and Now Playing's Delete take them as any
 * playlist's and they last across a restart in database.db; Save as Playlist gives them a header
 * of their own (playlists.c otg_save). Playback > Library's On-The-Go switch (ringnav_otg) hides
 * its row and the hold menu's Add to On-The-Go.
 *
 * Clicks run on the next loop pass (pv_run), as the queue menu's do, so a rebuild never happens
 * under its own click. The rows' glyphs and right-hand text are painted here (pv_paint); Add Songs
 * (add_songs.c) is a page of its own. */
#include "navigation.h"

#define PV_ICON "_pv_icon"   /* an action row's glyph: tools/art.py PL_ACTIONS' stem */
#define PV_RIGHT "_pv_right" /* text on the right column: an artist, a length, a count */
#define PV_ICON_X 26   /* an action glyph's left: centred in a 22 px column at the text inset */
#define PV_ACTION_X 54 /* an action's label, after its glyph */
#define PV_GAP 8
#define PV_PX_15 TYPE(15) /* AWTK's size for the design's 15 px */

enum { PV_SHUFFLE = 1000, PV_ADD, PV_SAVE, PV_CLEAR };

static struct {
    void *page, *view, *songs;
    int dest, selected, pending, acts, act[4];
    unsigned timer;
    char name[128];
} pv __attribute__((section(".scratch")));

/* On-The-Go's song count, for its row on Playlists. */
static int otg_query(void *unused) { return getMusicByPlayList(ON_THE_GO); }
int otg_count(void) {
    int n;
    void *songs = staged(otg_query, (void *)0, &n);
    n = songs ? (int)deque_size(songs) : 0;
    if (songs) deque_destroy(songs);
    return n;
}

/* Playlist dest's songs in its own order (stock's Time added, as stored), a staged query; shared
 * with add_songs.c, whose removal takes an entry's ID from it. */
int playlist_query(void *dest) { return sort_playlist_addtime((int)(long)dest); }
/* The songs as the page shows and plays them: a playlist's in its Sort's order (getMusicByPlayList,
 * hooked by playlist_songs.c), On-The-Go's as stored. */
static int pv_query(void *dest) {
    int d = (int)(long)dest;
    return d == ON_THE_GO ? sort_playlist_addtime(d) : getMusicByPlayList(d);
}
static void pv_reload(void) {
    int n;
    if (pv.songs) deque_destroy(pv.songs);
    pv.songs = staged(pv_query, (void *)(long)pv.dest, &n);
}
static int pv_size(void) { return pv.songs ? (int)deque_size(pv.songs) : 0; }

/* A row of the page's kind in view at index: caption, an action's glyph (icon) or the text on
 * its right column (right). The label leaves room for both; pv_paint draws them. */
static void *pv_row(void *view, int index, const char *caption, const char *icon, const char *right,
                    int (*click)(void *, void *), int ctx) {
    void *item = page_row_detail(view, index, caption, 0, click);
    void *label = widget_get_child(item, 0);
    /* the click's ctx is page_row_detail's index: ours replaces it */
    widget_off_by_func(item, EVT_CLICK, (void *)click, (void *)(long)index);
    widget_on(item, EVT_CLICK, click, (void *)(long)ctx);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_prop_int(label, "loop", 0);
    if (icon) {
        widget_set_prop_str(item, PV_ICON, icon);
        widget_set_prop_int(label, "style:normal:text_color", (int)RGBA(scheme()->tones[0]));
        widget_move_resize(label, PV_ACTION_X - 2, 0, 375 - geom_val(V_RIGHT_X) - PV_ACTION_X,
                           I(label, W_H));
    }
    if (right && *right) widget_set_prop_str(item, PV_RIGHT, right);
    return item;
}

/* The page under rows: its action rows, then its songs; the selection where it was. */
static int pv_click(void *ctx, void *event);
static void pv_show(void) {
    int n = pv_size(), otg = pv.dest == ON_THE_GO, k = 0;
    char title[512];
    pv.acts = 0;
    if (n) pv.act[pv.acts++] = PV_SHUFFLE;
    pv.act[pv.acts++] = PV_ADD;
    if (otg && n) pv.act[pv.acts++] = PV_SAVE, pv.act[pv.acts++] = PV_CLEAR;
    int rows = pv.acts + n;
    pv.view = page_list(pv.page, pv.page, 0, pv.name, rows, 48);
    static const char *const words[] = { "Shuffle", "Add Songs", "Save as Playlist", "Clear" };
    static const char *const icons[] = { "shuffle", "add", "save", "clear" };
    for (; k < pv.acts; ++k)
        pv_row(pv.view, k, words[pv.act[k] - PV_SHUFFLE], icons[pv.act[k] - PV_SHUFFLE], 0,
               pv_click, pv.act[k]);
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(pv.songs, (unsigned)i);
        playing_mark(pv_row(pv.view, k + i, track_name(title, sizeof title, r), 0, P(r, REC_ARTIST),
                            pv_click, i),
                     r, 1); /* pv_paint draws its bars */
    }
    /* no songs: the empty state under Add Songs, compact (a row above it: no glyph), no button of
     * its own as Add Songs is the row above (oled-design.md Empty and offline) */
    static const empty_t none = { 0, "No Songs Yet", "Choose Add Songs to fill it", 0, 0, 0 },
                         otg_none = { 0, "No Songs Yet", "Hold Play/Pause on a song to add it", 0, 0, 0 };
    if (!n)
        empty_state(pv.page, feedback_below(widget_get_child(pv.view, rows - 1)),
                    otg ? &otg_none : &none, 0);
    if (pv.selected >= rows) pv.selected = rows ? rows - 1 : 0;
    if (rows) ringnav_select(pv.view, pv.selected, rows);
}

static int pv_run(const void *unused) {
    pv.timer = 0;
    if (!pv.page) return 0;
    int a = pv.pending, n = pv_size();
    char said[80];
    if (a == PV_SHUFFLE && n) {
        void *all = staged(playlist_query, (void *)(long)pv.dest, &n);
        shuffle_play(all);
        deque_destroy(all);
    } else if (a == PV_ADD) {
        add_songs(pv.dest, pv.name);
    } else if (a == PV_SAVE) {
        int id = otg_save(said + 9, sizeof said - 9);
        if (id < 0) {
            toast("Could not save playlist");
            return 0;
        }
        memcpy(said, "Saved as ", 9);
        g_delete_flag = 1; /* Playlists reads its rows again */
        toast(said);
        navigator_back();
    } else if (a == PV_CLEAR) {
        if (!playlist_confirm("Clear On-The-Go?", "Clear")) return 0;
        if (!otg_clear()) {
            toast("Could not save playlist");
            return 0;
        }
        g_delete_flag = 1;
        pv.selected = 0;
        pv_reload();
        pv_show();
        toast("On-The-Go cleared");
    } else if (a >= 0 && a < n) {
        void *r = deque_at(pv.songs, (unsigned)a);
        if (catalogue_missing(P(r, REC_PATH))) {
            toast("Storage unavailable");
            return 0;
        }
        /* stock's own playlist play: its list's class with the playlist's cd_num */
        I(g_local_classinfo_save, 4) = pv.dest;
        play_folder(pv.songs, a, 0xff13);
    }
    return 0;
}
static int pv_click(void *ctx, void *event) {
    if (pv.page && !pv.timer) {
        pv.pending = (int)(long)ctx;
        pv.selected = widget_get_prop_int(pv.view, SEL, 0);
        pv.timer = timer_add(pv_run, (void *)0, 0);
    }
    return 0;
}

static int pv_closed(void *ctx, void *event) {
    stop_timer(&pv.timer);
    if (pv.songs) deque_destroy(pv.songs);
    memset(&pv, 0, sizeof pv);
    return 0;
}
/* Back on top (Now Playing closed, a song's Delete): the songs read again. */
static int pv_front(void *ctx, void *event) {
    if (pv.page && pv.view) {
        pv.selected = widget_get_prop_int(pv.view, SEL, pv.selected);
        pv_reload();
        pv_show();
    }
    return 0;
}

/* Playlist dest's page (0 to 99, or ON_THE_GO), titled name. */
void playlist_page_open(int dest, const char *name) {
    if (pv.page || dest < 0 || dest > ON_THE_GO) return;
    pv.page = page_open("playlistsongs_page", pv_closed, page_keyup);
    if (!pv.page) return;
    pv.dest = dest;
    tk_snprintf(pv.name, sizeof pv.name, "%s", name);
    widget_on(pv.page, EVT_WINDOW_FOREGROUND, pv_front, 0);
    pv_reload();
    pv_show();
}

/* The hold menu's Remove (qm_song_hold): the confirmation card asks first; Remove (row 1) takes the
 * song held out, if it is still the selected row. */
static void *pv_held __attribute__((section(".scratch")));
static void pv_remove_answer(void *ctx, int row, int toggle_on) {
    int at = pv.page ? widget_get_prop_int(pv.view, SEL, -1) - pv.acts : -1;
    void *r = at >= 0 && at < pv_size() ? deque_at(pv.songs, (unsigned)at) : 0;
    (void)ctx, (void)toggle_on;
    if (row != 1 || !r || r != pv_held) return;
    if (!playlist_remove(pv.dest, r)) {
        toast("Could not save playlist");
        return;
    }
    g_delete_flag = 1;
    pv.selected = at + pv.acts;
    pv_reload();
    pv_show();
    toast("Removed");
}
static void pv_remove(void) {
    static const char *const rows[] = { "Cancel", "Remove" };
    static char name[128];
    if (!pv.page || !pv_held) return;
    track_name(name, sizeof name, pv_held);
    confirm(&(confirm_t){ .title = "Remove \"%s\"?",
                          .name = name,
                          .body = "It stays in your library.",
                          .rows = rows,
                          .n_rows = 2,
                          .kind = CONFIRM_DESTRUCTIVE,
                          .back = -1,
                          .answer = pv_remove_answer });
}
/* A song row held on the page, by Play/Pause (playlist_page_hold) or Centre (hold_menu.c
 * qm_centre_hold): a song's whole menu, as a Library song's, with Remove from Playlist (or
 * On-The-Go) last; 1 when it is on its way, 0 on an action row or another page. */
int playlist_page_song_hold(void *top) {
    int at = pv.page && top == pv.page ? widget_get_prop_int(pv.view, SEL, -1) - pv.acts : -1;
    if (at < 0 || at >= pv_size()) return 0;
    pv_held = deque_at(pv.songs, (unsigned)at);
    return qm_song_hold(pv.page, pv.songs, (unsigned)at,
                        pv.dest == ON_THE_GO ? "Remove from On-The-Go" : "Remove from Playlist",
                        pv_remove);
}
/* ringnav_keylong's Play/Pause hold on the page: a song's menu; 1 when it is ours, an action row's
 * too (nothing, its release dropped). */
int playlist_page_hold(void *top) {
    if (!pv.page || top != pv.page) return 0;
    playlist_page_song_hold(top);
    return 1;
}

/* A row's own ink: lit, the list's selection shown on it. */
static int pv_lit(void *item) {
    void *list = P(item, W_PARENT);
    return !st.touch_mode && widget_get_prop_int(list, SEL, -1) == widget_index_of(item);
}

/* ringnav_paint's, for a row built by pv_row: an action's glyph and its label's accent, and the
 * right-hand text, white on the selection, after the playing song's bars where its row has them
 * (song_rows.c playing_row). The title's label ends PV_GAP short of it. */
void pv_paint(void *w, void *canvas) {
    void *win = pv.page ? window_of(w) : (void *)0;
    if (!win || win != pv.page || !P(canvas, CANVAS_LCD) ||
        tk_strcmp(widget_get_type(w), "list_item"))
        return;
    const char *icon = widget_get_prop_str(w, PV_ICON, ""),
               *right = widget_get_prop_str(w, PV_RIGHT, "");
    int on = pv_lit(w), h = I(w, W_H), end = 375 - geom_val(V_RIGHT_X);
    unsigned muted = on ? 0xffffff : tok(TOK_TERTIARY);
    void *label = widget_count_children(w) ? widget_get_child(w, 0) : (void *)0;
    char art[32];
    if (icon && *icon) {
        int ink = (int)RGBA(on ? 0xffffff : scheme()->tones[0]);
        tk_snprintf(art, sizeof art, "plact_%s_%s", icon, on ? "w" : "a");
        paint_art(w, canvas, theme_art(art), PV_ICON_X, (h - 18) / 2);
        /* written when it differs: the selection's own ink may have changed it */
        if (label && widget_get_prop_int(label, "style:normal:text_color", 0) != ink)
            widget_set_prop_int(label, "style:normal:text_color", ink);
        return;
    }
    end -= playing_row(w, canvas, end - 1, 0, on); /* the playing song's bars (song_rows.c) */
    if (right && *right)
        end -= ui_text(canvas, right, 0, PV_PX_15, muted, 1, end, h / 2, 150, 3) + PV_GAP;
    if (label && I(label, W_X) + I(label, W_W) != end && end > I(label, W_X))
        widget_resize(label, end - I(label, W_X), I(label, W_H));
}
