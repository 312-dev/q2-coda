/* Up Next (docs/oled-design.md#up-next, docs/internals.md#queue-menu): the queue from the song
 * playing on, as Apple Music's, in the menu sheet (menu_modal.c modal_sheet), which rises over the
 * page it opened from. The playing song heads it as the sheet's header, its cover, its title with
 * its level bars after it (moving while it plays, still while paused) and its artist and album,
 * never selected; then Playing Next, the songs Play Next, Play Last and a bump
 * queued (playback.c's pending list), under a heading whose Clear pill empties it; then the rest
 * of the queue in play order under "From <source>", the list, album or page it was played from
 * (un_source_take). Each song is a row of its cover, its title over its artist.
 *
 * The wheel's stops are Clear and the songs: Centre plays a song (as stock's View Queue does,
 * player_start_queue) and Clear empties Playing Next (playback_next_clear), no confirm. A queued
 * song past the first has a bump arrow: a tap on it, or Play/Pause held on its row (un_hold),
 * moves it to the top of Playing Next (playback_next_move), where an accent edge marks it a
 * moment. Centre held on a queued row lifts it onto a raised card, as the Library's Edit Library
 * does (library_edit.c): the wheel carries it among Playing Next's rows, each showing a grip,
 * Centre puts it down there and Return puts it back. The rest of the queue is the queue's own
 * order: no arrow, no lift, and Play/Pause does nothing on it. Centre held on one of its songs, or
 * a finger held on any queued song, opens the song's hold menu (un_menu_hold, centre_menu.c).
 * Nothing toasts. Return lets the
 * sheet fall away (un_back, menu_modal.c modal_close), as do a tap on the page above it and a drag
 * down on it (menu_modal.c modal_touch_close).
 *
 * A card app's playlist session (a Subsonic radio, an album streamed) shows its songs not yet
 * queued after the queue's, as pending rows (addon_pending: named and with covers from the
 * server's rows, not their downloads): the title dimmed and a cloud on the right while each waits
 * to download. Centre on one moves to it as Next does (addon_pending_play): Now Playing shows it
 * buffering at once and it plays once enough of it is there.
 *
 * The rows are taken again whenever the queue moves on (un_poll, once a second: the song playing,
 * the queue's size and loads, Playing Next, the session's list), so songs ending leave the list as
 * they play and a radio's new songs join it.
 * ponytail: UN_ROWS rows at most, under ringnav's MAX_ENTRIES (512) stops; a table_client page for
 * more. A touch can't drag a lifted row (Centre and the wheel only). */
#include "navigation.h"
#include "playback.h"

#define UN_ROWS 500
#define UN_ROW_H 46  /* a song's row */
#define UN_HEAD_H 24 /* Playing Next's heading */
#define UN_FROM_H 26 /* "From ..." and the gap over it */
#define UN_ART 36    /* a row's cover, UN_ART_DY down, its corners UN_ART_R */
#define UN_ART_DY 5
#define UN_ART_R 4
#define UN_TEXT_DX 46 /* the words this far right of the text column */
#define UN_TITLE_Y 15 /* the title's middle in its row, the artist's */
#define UN_ARTIST_Y 32
#define UN_PILL_H 22 /* Clear: a pill ending UN_PILL_END from the screen's right, as the bar */
#define UN_PILL_END 16
#define UN_PILL_PAD 10
#define UN_PILL_REST RGB_RAISED_2
#define UN_PILL_INK 0x7fa6f5
#define UN_PILL_REST_L 0xe5e5ea /* Light Mode's: the tab track's grey, the accent's words */
#define UN_BAR_LO 5             /* the level bars, UN_BAR_LO to UN_BAR_HI px (ui_kit.h lvl_h16) */
#define UN_BAR_HI 12
#define UN_POLL_MS 1000
#define UN_HOLD_MS 550  /* Centre held this long on a queued row lifts it */
#define UN_FRESH_MS 700 /* a bumped row's accent edge */
#define UN_LIFT_RGB RGB_RAISED_2
#define UN_LIFT_ART RGB_OFF     /* a placeholder cover on the lifted card */
#define UN_BUMP_ZONE 64         /* a tap this near the screen's right edge is on a row's arrow */
#define UN_CLOUD_PX 18          /* a pending row's cloud (catalogue.c CAT_ART_PX) */
#define UN_PENDING 40           /* a session's pending rows shown at most */
#define UN_PEND_ROW 0x80000000u /* un.idx's mark of a pending row: un.pend's index under it */

void ar_corners(void *canvas, const rect_t *r, int R, unsigned behind); /* artist_page.c */
void *modal_sheet(int (*back)(void *, void *), int (*gone)(void *, void *));
void modal_head_bars(void), modal_close(void *win), modal_touch_close(void *win),
    modal_corners(void *canvas, const rect_t *r, int R);

static struct {
    void *page, *view;        /* the sheet and its rows' scroll view, 0 when closed */
    void *now, *head, *clear; /* the header's space, Playing Next's heading and its Clear */
    unsigned idx[UN_ROWS];    /* the rows' queue indices: Playing Next's first, npn of them */
    unsigned n, npn, sig;     /* the rows, how many are queued, what they were taken from */
    unsigned poll, hold, fresh_timer;
    int lifted;        /* a lifted queued row's place + 1, 0 none */
    int lift_from;     /* ... and where it was lifted from */
    int centre;        /* the Centre hold that lifted one: its release is taken */
    int fresh;         /* the bumped row's place + 1 while its edge shows */
    lvl_clock_t clock; /* the level bars' time along their loop */
    rect_t bars_at;    /* their box at the last paint, on screen (w 0: none painted) */
    char source[128];  /* the queue's source (un_source_take) */
    void **pend;       /* the session's pending songs (addon_pending), npend of them */
    unsigned npend;
} un __attribute__((section(".scratch")));

/* A pick that loads a queue (playback.c ringnav_load): where it came from, for "From ...": an
 * album's (class 0xff1x) its album, else the page in front's title as the bar shows it. */
void un_source_take(void *q, int at, int type) {
    void *r = q && (unsigned)at < deque_size(q) ? deque_at(q, (unsigned)at) : (void *)0;
    const char *album = r && (type & 0xfff0) == 0xff10 ? P(r, REC_ALBUM) : (const char *)0;
    un.source[0] = 0;
    if (album && *album)
        tk_snprintf(un.source, sizeof un.source, "%s", album);
    else if (st.bar_title && st.bar_titled)
        widget_get_text_utf8(st.bar_title, un.source, sizeof un.source);
}

/* The rows' signature: the song playing, the queue's size and loads, the mode, Playing Next, the
 * album covers fetched, the session's pending songs. */
static unsigned un_sig(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pend[64],
        k = playback_next_rows(pend, 64),
        v[] = { (unsigned)MCL(MCL_POS),  q ? deque_size(q) : 0,        playback_loads,
                (unsigned)MCL(MCL_MODE), (unsigned)playback_option(0), k,
                album_cover_gen(),       addon_pending_sig() };
    return hash_bytes(fnv(FNV_SEED, (const unsigned char *)""), (const unsigned char *)v,
                      sizeof v) ^
           hash_bytes(FNV_SEED, (const unsigned char *)pend, k * sizeof *pend);
}

/* a row's cover as a file:// URL in url (520 bytes), or 0 for the placeholder (library_artist.c's):
 * its album's (album_cover.c: Subsonic's, Cover Flow's, the one Now Playing shows, or one fetched
 * from a copy on the card, which un_sig's album_cover_gen then shows) */
static const char *un_cover(void *r, char *url) {
    return album_cover_url(&r, 1, url, 520) ? url : (const char *)0;
}

static void *un_record(unsigned at) {
    void *q = P(mcl_pdeqplaylist, 0);
    if (at & UN_PEND_ROW) return (at &= ~UN_PEND_ROW) < un.npend ? un.pend[at] : (void *)0;
    return q && at < deque_size(q) ? deque_at(q, at) : (void *)0;
}

/* The covers the rows loaded, let go of; the player's cover files too, which a row of the playing
 * song's album shows and the next song's cover overwrites */
static void un_unload(void) {
    char url[520];
    now_cover_forget();
    for (unsigned i = 0; i < un.n; ++i) {
        void *r = un_record(un.idx[i]);
        if (r && un_cover(r, url)) image_manager_unload_bitmap_by_name(image_manager(), url);
    }
}

static int un_click(void *ctx, void *event);
static int un_clear(void *ctx, void *event);

/* A song's row: its cover as an image, the rest painted (upnext_paint) */
static void *un_row(void *parent, void *r, int h, int stop, int (*click)(void *, void *)) {
    char url[520];
    void *item = list_item_create(parent, 0, 0, 375, h);
    widget_use_style(item, "s_listitem_black");
    widget_set_prop_int(item, "style:normal:bg_color", 0); /* on the sheet */
    widget_set_prop_int(item, "_un_stop", stop);
    char name[512];
    const char *title = r ? now_tag(r, REC_TITLE) : (const char *)0;
    if (r && (!title || !*title)) title = track_name(name, sizeof name, r);
    widget_set_prop_str(item, "_un_title",
                        title ? title : ""); /* what it reads as (tests, a reader) */
    const char *cover = r ? un_cover(r, url) : (const char *)0;
    if (cover) {
        void *img = image_create(item, geom_val(V_TEXT_X), UN_ART_DY, UN_ART, UN_ART);
        image_set_draw_type(img, 4); /* scaled to fit */
        image_base_set_image(img, cover);
    }
    widget_on(item, EVT_CLICK, click, (void *)(long)stop);
    return item;
}

/* The rows, taken again; sel the stop selected after, -1 the first song */
static void un_fill(int sel) {
    unsigned up[UN_ROWS], pend[64], n = playback_upcoming(up, UN_ROWS), k = 0, at, size;
    unsigned npn = playback_next_rows(pend, 64);
    un_unload();
    /* Playing Next first: the upcoming rows that are its, in order, are not repeated after it */
    while (k < n && k < npn && up[k] == pend[k]) ++k;
    for (unsigned i = 0; i < npn; ++i) un.idx[i] = pend[i];
    un.n = npn;
    for (unsigned i = k; i < n && un.n < UN_ROWS; ++i) un.idx[un.n++] = up[i];
    addon_pending_free(un.pend, un.npend); /* the old rows' covers went with un_unload */
    un.pend = addon_pending(UN_ROWS - un.n < UN_PENDING ? UN_ROWS - un.n : UN_PENDING, &un.npend);
    for (unsigned i = 0; i < un.npend; ++i) un.idx[un.n++] = UN_PEND_ROW | i;
    un.npn = npn < UN_ROWS ? npn : UN_ROWS;
    un.sig = un_sig();
    void *view = un.view;
    widget_destroy_children(view);
    /* the sheet's header space (menu_modal.c MM_HEAD): the playing song, drawn by the sheet */
    void *now = queue_now(&at, &size);
    char name[512], sub[200];
    const char *title = now ? now_tag(now, REC_TITLE) : (const char *)0,
               *artist = now ? now_tag(now, REC_ARTIST) : (const char *)0,
               *album = now ? P(now, REC_ALBUM) : (const char *)0;
    if (now && (!title || !*title)) title = track_name(name, sizeof name, now);
    un.now = label_create(view, 0, 0, 375, 0);
    widget_set_text_utf8(un.now, "");
    widget_set_name(un.now, "_mm_head");
    widget_set_prop_str(un.now, "_un_title", title ? title : ""); /* what it reads as */
    if (artist && *artist && album && *album)
        tk_snprintf(sub, sizeof sub, "%s \xc2\xb7 %s", artist, album);
    else
        tk_snprintf(sub, sizeof sub, "%s", artist && *artist ? artist : album ? album : "");
    modal_head(title ? title : "", sub, now);
    modal_head_bars();
    un.head = un.clear = (void *)0;
    int stop = 0, first = -1;
    if (un.npn) {
        un.head = label_create(view, 0, 0, 375, UN_HEAD_H);
        widget_use_style(un.head, "s_listitem_black");
        widget_set_prop_int(un.head, "style:normal:bg_color", 0); /* on the sheet */
        un.clear = list_item_create(un.head, 0, 0, 0, UN_PILL_H); /* placed when painted */
        /* its corners the sheet's, and no frame: the row style's border shows without its fill */
        widget_set_prop_int(un.clear, "style:normal:bg_color", 0);
        widget_set_prop_int(un.clear, "style:normal:border_color", 0);
        widget_set_prop_int(un.clear, "_un_stop", stop++);
        widget_on(un.clear, EVT_CLICK, un_clear, (void *)0);
    }
    for (unsigned i = 0; i < un.n; ++i) {
        if (i == un.npn) {
            void *from = label_create(view, 0, 0, 375, UN_FROM_H);
            widget_use_style(from, "s_listitem_black");
            widget_set_prop_int(from, "style:normal:bg_color", 0);
            widget_set_prop_int(from, "_un_from", 1);
        }
        if (first < 0) first = stop;
        un_row(view, un_record(un.idx[i]), UN_ROW_H, stop++, un_click);
    }
    modal_frame(un.page, P(view, W_PARENT)); /* the rows at their own heights (MM_OWN) */
    widget_layout_children(view);
    if (un.clear) { /* Clear's pill on the heading's right, ending with the selection bar */
        int w = 0;
        void *canvas = widget_get_canvas(un.head);
        if (canvas) {
            unsigned buf[8];
            unsigned m = ui_wide("Clear", buf, 8);
            canvas_set_font(canvas, "inter-medium", TYPE(15));
            w = (int)(canvas_measure_text(canvas, buf, m) + 0.5f);
        }
        w = (w ? w : 38) + 2 * UN_PILL_PAD;
        widget_move_resize(un.clear, 375 - UN_PILL_END - w, 0, w, UN_PILL_H);
    }
    if (stop)
        ringnav_select(view, sel < 0 ? (first < 0 ? 0 : first) : sel < stop ? sel : stop - 1, stop);
    widget_invalidate_force(un.page, (void *)0);
}

/* The row at stop, the place in un.idx it shows, -1 for Clear or none */
static int un_place(int stop) { return stop - (un.npn ? 1 : 0); }

/* A song's row pressed (Centre or a tap): it plays, as stock's View Queue row, and the sheet
 * closes (with nothing to play to it stays, its start refused); a tap on a queued row's arrow
 * bumps it instead. */
static int un_bump(int k);
static int un_click(void *ctx, void *event) {
    int k = un_place((int)(long)ctx);
    if (k < 0 || (unsigned)k >= un.n || un.lifted) return 0;
    if (event && I(event, EVENT_TYPE) == EVT_CLICK && st.touch_mode && k > 0 &&
        (unsigned)k < un.npn && I(event, EVENT_X) >= 375 - UN_BUMP_ZONE)
        return un_bump(k), 0;
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned at = un.idx[k];
    int pend = (at & UN_PEND_ROW) != 0, keeps = !pend && q && playback_keeps(q, (int)at);
    /* A start with nothing to play to (the playing song's own row starts nothing; a paused one's
     * plays it on): stock's message, and Up Next stays with its selection (output_rule.c) */
    if ((!keeps || mclGetPlayStatus() == 3) && check_forbid_play_no_headset(0)) return 0;
    if (pend) { /* a song still to download: Now Playing moves to it, as a Next */
        void *r = un_record(at);
        if (r) addon_pending_play(P(r, REC_PATH));
        navigator_back();
        return 0;
    }
    if (!keeps && q && at < deque_size(q)) playback_pick(), player_start_queue((int)at);
    navigator_back();
    if (keeps) playback_np();
    return 0;
}

/* Clear: Playing Next empties, the selection on the first song left */
static int un_clear(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (!un.page || un.lifted) return 0;
    playback_next_clear();
    un_fill(-1);
    return 0;
}

static int un_fresh_due(const void *info) {
    (void)info;
    un.fresh_timer = 0;
    un.fresh = 0;
    if (un.page) widget_invalidate_force(un.page, (void *)0);
    return 0;
}

/* Queued row k to the top of Playing Next, its edge lit a moment; the selection stays where it is,
 * on the row that took its place, so the next one up bumps as well */
static int un_bump(int k) {
    if (k <= 0 || (unsigned)k >= un.npn || !playback_next_move((unsigned)k, 0)) return 0;
    un_fill(widget_get_prop_int(un.view, SEL, -1));
    un.fresh = 1;
    rearm(&un.fresh_timer, un_fresh_due, UN_FRESH_MS);
    return 1;
}

/* A Play/Pause hold in the sheet (navigation.c ringnav_keylong): on a queued row past the first it
 * bumps it; on any other row it does nothing. 1 when Up Next took it. */
static int un_hold_due(const void *info) {
    (void)info;
    if (un.page && !un.lifted) un_bump(un_place(widget_get_prop_int(un.view, SEL, -1)));
    return 0;
}
int un_hold(void *top) {
    if (!top || top != un.page) return 0;
    timer_add(un_hold_due, (void *)0, 0);
    return 1;
}

/* Centre or a finger held on a song's row (hold_menu.c qm_centre_hold, qm_touch_hold): the song's
 * hold menu over the queue, as a playlist page's song gets it (qm_song_hold), at the row's queue
 * index. Centre (centre) leaves a Playing Next row to its lift (un_keydown); a pending row (a card
 * app's song not yet queued) and the header have none. 1 when the menu is on its way. */
int un_menu_hold(void *top, int centre) {
    if (!top || top != un.page || un.lifted) return 0;
    int k = un_place(widget_get_prop_int(un.view, SEL, -1));
    void *q = P(mcl_pdeqplaylist, 0);
    if (k < 0 || (unsigned)k >= un.n || (centre && (unsigned)k < un.npn) || !q) return 0;
    unsigned at = un.idx[k];
    if ((at & UN_PEND_ROW) || at >= deque_size(q)) return 0;
    stop_timer(&un.hold);
    return qm_song_hold(un.page, q, at, (const char *)0, (void (*)(void))0);
}

/* The lifted row moved to place to of Playing Next: the view's children reordered (the row's
 * stop index is its place), the selection with it */
static void un_carry(int to) {
    int from = un.lifted - 1;
    if (to < 0 || (unsigned)to >= un.npn || to == from) return;
    unsigned x = un.idx[from], base = 2; /* the playing row and the heading come first */
    void *row = widget_get_child(un.view, base + (unsigned)from);
    for (int k = from; k > to; --k) un.idx[k] = un.idx[k - 1];
    for (int k = from; k < to; ++k) un.idx[k] = un.idx[k + 1];
    un.idx[to] = x;
    widget_restack(row, base + (unsigned)to);
    for (unsigned k = 0; k < un.npn; ++k) /* each row's stop, its place again */
        widget_set_prop_int(widget_get_child(un.view, base + k), "_un_stop", (int)k + 1);
    widget_layout_children(un.view);
    un.lifted = to + 1;
    ringnav_select(un.view, to + 1, (int)un.n + 1);
}

static int un_lift_due(const void *info) {
    (void)info;
    un.hold = 0;
    int k = un_place(widget_get_prop_int(un.view, SEL, -1));
    if (!un.page || window_manager_get_top_window(window_manager()) != un.page || k < 0 ||
        (unsigned)k >= un.npn)
        return 0;
    if (st.centre_hold) { /* stock's own hold, Shut Down?, never comes */
        timer_remove(st.centre_hold);
        st.centre_hold = 0;
    }
    un.centre = 1;
    un.lifted = un.lift_from = k + 1;
    /* the raised card: the row's own background, so its cover stays over it (un_fill's rows are
     * new, so a drop or a cancel ends it) */
    void *row = widget_get_child(un.view, 2 + (unsigned)k);
    widget_set_prop_int(row, "style:normal:bg_color", (int)RGBA(UN_LIFT_RGB));
    widget_set_prop_int(row, "style:normal:round_radius", 10);
    widget_invalidate_force(un.page, (void *)0);
    return 0;
}

/* navigation.c's key-down: Centre pressed on a queued row starts the hold that lifts it */
void un_keydown(unsigned key) {
    if (key != KEY_CENTER || !un.page || un.lifted || un.hold || st.touch_mode ||
        window_manager_get_top_window(window_manager()) != un.page)
        return;
    int k = un_place(widget_get_prop_int(un.view, SEL, -1));
    if (k >= 0 && (unsigned)k < un.npn) un.hold = timer_add(un_lift_due, (void *)0, UN_HOLD_MS);
}

/* navigation.c's key filter, a key's release on top: 1 if Up Next took it. The release of the
 * hold that lifted a row; with one lifted, the wheel carries it (owned: its press was silenced, so
 * a move clicks), Centre puts it down and Playing Next takes the order, Return puts it back. */
int un_key(void *top, unsigned key, int owned) {
    if (key == KEY_CENTER) {
        stop_timer(&un.hold);
        if (un.centre) {
            un.centre = 0;
            *(volatile unsigned char *)KEY_LOCKOUT = 0; /* the wheel at once, as a click's */
            return 1;
        }
    }
    if (!un.lifted || !top || top != un.page) return 0;
    int at = un.lifted - 1;
    if (key == KEY_CENTER || key == KEY_RETURN) {
        if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
        int from = un.lift_from - 1;
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
        un.lifted = 0;
        if (key == KEY_CENTER && at != from) playback_next_move((unsigned)from, (unsigned)at);
        un_fill(key == KEY_CENTER ? at + 1 : from + 1);
        return 1;
    }
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (!dir) return 0;
    if ((unsigned)(at + dir) < un.npn) {
        un_carry(at + dir);
        if (owned) buzzeer_switch(1);
    }
    widget_invalidate_force(un.page, (void *)0);
    return 1;
}

static void un_bars_sync(void);
/* Once a second: the queue moved on (un_sig) takes the rows again, the selection's stop kept; the
 * level bars started again once the song plays */
static int un_poll(const void *info) {
    (void)info;
    if (!un.page) return 0;
    un_bars_sync();
    if (!un.lifted && !un.hold && un_sig() != un.sig)
        un_fill(widget_get_prop_int(un.view, SEL, -1));
    return 8; /* RET_REPEAT */
}

/* Whether the level bars move: the sheet lit and in front, the song playing, motion allowed
 * (MO_DECOR: still under Reduce Motion). Their clock runs only then. */
static int un_bars_moving(void) {
    return un.page && g_backlight_status &&
           window_manager_get_top_window(window_manager()) == un.page && mclGetPlayStatus() == 2 &&
           motion_ms(LVL_STEP_MS, MO_DECOR);
}
/* The shared loop timer's (ui_kit.c motion_loop), MO_LOOP_MS apart while the bars move: their box
 * as last painted repainted, on the sheet's window; 0 (dropped) once they stop. */
static int un_bars_frame(void) {
    int on = un_bars_moving();
    lvl_run(&un.clock, on);
    if (on && un.bars_at.w) {
        rect_t r = un.bars_at;
        r.x -= I(un.page, W_X), r.y -= I(un.page, W_Y);
        widget_invalidate_force(un.page, &r);
    }
    return on;
}
static void un_bars_sync(void) {
    int on = un_bars_moving();
    lvl_run(&un.clock, on);
    if (on) motion_loop(un_bars_frame);
}

static int un_closed(void *ctx, void *event) {
    (void)ctx, (void)event;
    un_unload();
    addon_pending_free(un.pend, un.npend);
    un.pend = (void **)0, un.npend = 0;
    stop_timer(&un.poll);
    stop_timer(&un.hold);
    stop_timer(&un.fresh_timer);
    un.page = un.view = un.now = un.head = un.clear = (void *)0;
    un.lifted = un.centre = un.fresh = 0;
    un.bars_at.w = 0;
    return 0;
}

/* Return lets the sheet fall away (a lifted row's Return is un_key's, before it) */
static int un_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    modal_close(dialog);
    return STOP;
}

/* Up Next opened in the sheet, named as its page was (contexts.inc, navigation.c own_order) */
void up_next(void) {
    if (un.page || !(un.page = modal_sheet(un_back, un_closed))) return;
    widget_set_name(un.page, "upnext_page");
    widget_set_prop_int(un.page, "_mm_own", 1); /* menu_modal.c MM_OWN: the rows' own heights */
    modal_touch_close(un.page); /* a tap above the sheet, or a drag down on it, closes it too */
    if (!(un.view = widget_lookup(un.page, "scroll_view", 1))) {
        window_close(un.page);
        return;
    }
    un_fill(-1);
    un.poll = timer_add(un_poll, (void *)0, UN_POLL_MS);
    un_bars_sync();
}

/* Whether w's place on the list is lit: the selection, shown */
static int un_lit(void *w) {
    return !st.touch_mode &&
           widget_get_prop_int(un.view, SEL, -1) == widget_get_prop_int(w, "_un_stop", -2);
}

/* The playing song's level bars from x, standing on base, in the accent: three, 3 px wide and 4.5
 * apart, at the clock's time (the sheet's header, menu_modal.c modal_after); their box kept for
 * un_bars_frame. */
void un_bars(void *canvas, int x, int base) {
    unsigned t = lvl_time(&un.clock), ink = RGBA(scheme()->tones[0] & 0xffffffu);
    for (int i = 0; i < 3; ++i)
        lvl_fill(canvas, x + i * 9 / 2, base, 3, lvl_h16(i, t, UN_BAR_LO, UN_BAR_HI), ink);
    un.bars_at = (rect_t){ x + I(canvas, CANVAS_X), base - UN_BAR_HI - 1 + I(canvas, CANVAS_Y), 12,
                           UN_BAR_HI + 1 };
    un_bars_sync();
}

#define UN_GRIP_X 5 /* the grip's middle this far left of the right column, as Edit Library's */

/* ringnav_paint, after a widget's own paint: Up Next's rows, headings and Clear */
void upnext_paint(void *w, void *canvas) {
    if (!un.page || !w || !P(canvas, CANVAS_LCD)) return;
    void *parent = P(w, W_PARENT);
    if ((parent != un.view && w != un.clear) || w == un.now) return; /* the header: the sheet's */
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR),
             accent = scheme()->tones[0] & 0xffffffu;
    int x = geom_val(V_TEXT_X), tx = x + UN_TEXT_DX, end = 375 - geom_val(V_RIGHT_X), h = I(w, W_H);
    if (w == un.clear) { /* Light Mode's at rest: grey, the accent's words, final colours */
        int on = un_lit(w), light = ipod_light() && !on, raw = st.raw_ink;
        rect_t r = { 0, 0, I(w, W_W), h };
        st.raw_ink = light;
        fill_box(canvas, &r,
                 RGBA(on      ? accent
                      : light ? UN_PILL_REST_L
                              : UN_PILL_REST),
                 UN_PILL_H / 2);
        ui_text(canvas, "Clear", 1, TYPE(15),
                on      ? 0xffffff
                : light ? accent
                        : UN_PILL_INK,
                1, I(w, W_W) - UN_PILL_PAD, h / 2, I(w, W_W), 3);
        st.raw_ink = raw;
    } else if (w == un.head) {
        ui_text(canvas, "Playing Next", 1, TYPE(13), RGB_SECONDARY, 0, x, h / 2, 250, 2);
    } else if (widget_get_prop_int(w, "_un_from", 0)) {
        char from[160];
        tk_snprintf(from, sizeof from, un.source[0] ? "From %s" : "Next in Queue", un.source);
        ui_text(canvas, from, 1, TYPE(13), RGB_SECONDARY, 0, x, 2 + (h - 2) / 2, end - x, 2);
    } else {
        int stop = widget_get_prop_int(w, "_un_stop", -2), k = un_place(stop), on = un_lit(w),
            lifted = un.lifted && k == un.lifted - 1;
        void *r = k >= 0 && (unsigned)k < un.n ? un_record(un.idx[k]) : (void *)0;
        if (!r) return;
        rect_t art = { x, UN_ART_DY, UN_ART, UN_ART };
        if (!widget_count_children(w)) /* no cover: a placeholder, a shade lighter on the card */
            fill_box(canvas, &art, RGBA(lifted ? UN_LIFT_ART : UN_PILL_REST), UN_ART_R);
        else if (lifted || on)
            ar_corners(canvas, &art, UN_ART_R, lifted ? UN_LIFT_RGB : accent);
        else /* on the sheet: the material's corners */
            modal_corners(canvas, &art, UN_ART_R);
        char name[512];
        const char *title = now_tag(r, REC_TITLE), *artist = P(r, REC_ARTIST);
        if (!title || !*title) title = track_name(name, sizeof name, r);
        int queued = k >= 0 && (unsigned)k < un.npn, right = un.lifted && queued ? 30 : 0;
        int pending = (un.idx[k] & UN_PEND_ROW) != 0;
        if (!right && ((queued && k > 0) || pending)) right = 30; /* the arrow's or cloud's room */
        ui_text(canvas, title, lifted, lifted ? TYPE(18) : TYPE(17),
                pending && !on ? RGB_SECONDARY : 0xffffff, on, tx, UN_TITLE_Y, end - right - tx, 2);
        if (artist && *artist)
            ui_text(canvas, artist, 0, TYPE(13), on || lifted ? 0xffffff : RGB_TERTIARY, on, tx,
                    UN_ARTIST_Y, end - right - tx, 2);
        if (pending) /* waiting to download: the cloud a server song's row has */
            paint_art(w, canvas,
                      theme_art(on             ? "mark_cloud_w"
                                : ipod_light() ? "mark_cloud_l"
                                               : "mark_cloud_d"),
                      end - 8 - UN_CLOUD_PX / 2, h / 2 - UN_CLOUD_PX / 2);
        else if (un.lifted && queued)
            grip_mark(canvas, 375 - geom_val(V_RIGHT_X) - UN_GRIP_X - page_ox(canvas, w), h / 2,
                      lifted || on ? 0xffffff : tok(TOK_TERTIARY), 100); /* list_marks.c's */
        else if (queued && k > 0) {
            unsigned bitmap[64]; /* bitmap_t: the bump arrow */
            const char *ink = on ? "bump_w" : ipod_light() ? "bump_l" : "bump_d";
            if (!widget_load_image(w, theme_art(ink), bitmap))
                canvas_draw_icon(canvas, bitmap, end - 8, h / 2);
        }
        if (queued && un.fresh == k + 1) { /* just bumped: an accent edge where it landed */
            rect_t edge = { 16, 6, 3, h - 12 };
            fill_box(canvas, &edge, RGBA(accent), 1);
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
}
