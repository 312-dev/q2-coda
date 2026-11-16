/* Search as an iPod classic's letter strip (list.entry.ipod, the OLED theme's;
 * docs/ipod.md#search): the Library's Search row opens a page with a pill low in the glass, the
 * typed text at its left and the alphabet passing under a fixed highlight, and what matches in the
 * list above it, filtered as each character goes in: the songs, then the artists, then the albums.
 * While the wheel is on the results, tabs above them (All, Songs, Artists, Albums, an iOS
 * segmented control) pick what the list holds: All the three groups, SS_GROUP of each at most,
 * the others one group in full. The tab picked stays while the text changes.
 *
 * Keys: the wheel walks the strip (A to Z, 0 to 9, a space, then delete, round again) and Centre
 * enters what is highlighted at the text's cursor; Previous and Next move the cursor along the
 * text, and Return erases the character before it (nothing, but the click, at the start). Next
 * with the cursor at the text's end moves to the results: the wheel moves their bar, Centre opens
 * a song, which plays with the songs after it as the queue, an artist (stock's artist page) or an
 * album (its tracks in Cover Flow). A turn up from the first result lands on the tabs, where
 * Centre and Next pick the next tab and Previous the one before, a turn down goes back to the
 * results and a turn up to the strip. Previous or Return on the results, and Return on the tabs,
 * go back to the strip, the text, cursor and tab kept. A held Return closes Search from any of
 * them (search_strip_hold, from Hold Return's compact_now_playing). Play/Pause stays the player's.
 *
 * The key pop (ss_pop_paint): when the wheel or a drag lands on a letter or digit, or a tap types
 * one, its key grows a bubble holding the letter large, and the key keeps it at the strip's size,
 * dimmed; it goes SS_POP_MS after the letters stop. A spin faster than SS_POP_FAST_MS a step shows
 * none until it stops, then one. The bubble and the letters are tools/art.py's images
 * (pop_images), drawn at whole pixels.
 *
 * Touch (ss_touch): a drag along the strip moves the alphabet a cell per 19 px as the finger goes,
 * popping as the wheel does, and types nothing; a tap on a letter, the faded ones at the run's ends
 * too, pops and types it; a tap on a tab picks it, a tap on a result opens or plays it, and a drag
 * up or down the results scrolls them a row per 32 px.
 *
 * The page is one sheet painted here (search_strip_paint), not rows: the strip and the list share
 * the wheel, and only one of them shows a selection at a time.
 *
 * The strip serves two of the playlist pages too, without the tabs: a playlist's Rename
 * (playlist_rename_open), whose one row is Done, and Add Songs' Search row (add_songs_search),
 * which lists songs alone. */
#include "navigation.h"

/* The sheet starts under the 58 px status row; on it, from the top: */
#define SS_PITCH 32   /* a result row */
#define SS_STRIP_X 40 /* the strip, a pill from x 40 to 335 and y 254 to 288 on the page */
#define SS_STRIP_Y 196
#define SS_STRIP_H 34
#define SS_STRIP_R 17
#define SS_ROWS (SS_STRIP_Y / SS_PITCH) /* result rows above the strip, from the sheet's top */
#define SS_TABS_X 16 /* the tabs: a 30 px segmented control in the first row, the rows' inset */
#define SS_TABS_Y 1
#define SS_TABS_H 30
#define SS_TABS_W (375 - 2 * SS_TABS_X)
#define SS_LIST_Y SS_PITCH /* the results' top under the tabs, and the rows that fit there */
#define SS_TAB_ROWS ((SS_STRIP_Y - SS_LIST_Y) / SS_PITCH)
#define SS_BOX_X 46  /* the typed text's black box, x 46 to 142, 5 px in from the strip's edges */
#define SS_BOX_R 142 /* ... */
#define SS_BOX_IN 5
#define SS_BOX_RADIUS 12
#define SS_TYPED_X 56 /* the typed text, the cursor 1 px after the characters before it */
#define SS_RUN_L 148  /* the letters' run, faded into the strip over SS_FADE px at each end */
#define SS_RUN_R 327
#define SS_FADE 22
#define SS_STEP 19       /* a letter's cell */
#define SS_LIT_X 228     /* the lit cell's left, so it is centred in the run */
#define SS_SIDE 4        /* cells each side of it inside the run */
#define SS_TEXT_MAX 48   /* characters typed, at most */
#define SS_TITLE_W 233   /* a result's title, at most; the artist takes the rest */
#define SS_ARTIST_MIN 40 /* an artist with less room than this is left out */
#define SS_RIGHT 347     /* the right column */
#ifndef CAT_MARK_W
#define CAT_MARK_W 20 /* the offline mark's ink (catalogue_mark_paint), centred on its x */
#endif
#define SS_MARK_X (SS_RIGHT - CAT_MARK_W / 2) /* a greyed row's mark: its ink ends at SS_RIGHT */
#define SS_MARK_GAP 12 /* a greyed row's text ends this far short of the mark's ink */
#define SS_ENTRIES 38  /* 26 letters, 10 digits, the space and delete */
#define SS_SPACE 36
#define SS_DELETE 37
/* AWTK sizes a face by its line, 1.21 em for Inter: the design's 20, 18, 19, 16 and 15 px */
#define SS_PX_ROW 24
#define SS_PX_TYPED 22
#define SS_PX_LETTER 23
#define SS_PX_NOTE 19
#define SS_PX_ARTIST 18
/* The key pop (tools/art.py POP_*): the bubble's image is the head's width, its foot the key's,
 * the key 18 px wide from the lit cell's left and SS_BOX_IN below the strip's top, as the lit
 * cell's accent box is; the big letters and the echoes are strips of a cell per strip character,
 * a head's and a key's box each, the echo's the strip's height */
#define SS_POP_W 30
#define SS_POP_H 66
#define SS_POP_HEAD 30
#define SS_POP_KEY_W 18
#define SS_POP_KEY_H 24
#define SS_POP_ECHO_H SS_STRIP_H
#define SS_POP_MS 300 /* the pop goes this long after the wheel's last step */
#define SS_POP_FAST_MS                                                                             \
    125 /* steps closer than this, 8 a second, are a spin: no pop until it stops */
/* songs, artists and albums listed at most on All (docs/ipod.md#search): the wheel moves its bar a
 * row a tick, 7 ticks a turn at the default Wheel Sensitivity, so the artists are under five turns
 * down; each artist and album is checked against those before it */
#define SS_GROUP 32
enum { SS_SONG, SS_ARTIST, SS_ALBUM };                                     /* a result's kind */
enum { SS_TAB_ALL, SS_TAB_SONGS, SS_TAB_ARTISTS, SS_TAB_ALBUMS, SS_TABS }; /* tab, kind + 1 */
enum { SS_ON_STRIP, SS_ON_LIST, SS_ON_TABS };                              /* what has the wheel */

static struct {
    void *page, *sheet, *songs; /* the page, what it paints on, the library's songs A to Z */
    void **match;               /* the results: songs, artists, albums; a record of each */
    unsigned char *kind;        /* ... each one's SS_* */
    int nmatch, letter, focus, sel, top; /* focus: SS_ON_* */
    int nsongs, tab, any; /* the songs first in match; the tab, SS_TAB_*; anything matches */
    int cur, by;          /* the cursor's place in text; ss_by_name's field */
    int rename;           /* Rename's playlist + 1 (playlist_rename_open), 0 for Search */
    int pick;             /* Add Songs' (add_songs_search): songs alone, Centre adds them */
    short down_x, down_y, pressed, swiped; /* a touch: where it went down, cells it moved */
    char text[SS_TEXT_MAX + 1];
    unsigned pop_timer, pop_step_at; /* the pop's timer; the wheel's last step on the strip */
    unsigned char pop;               /* 1 shown, 2 a spin's, shown when it stops */
} ss __attribute__((section(".scratch")));

/* The strip's entry e as text: a capital, a digit, or 0 for the space and delete (drawn). */
static unsigned ss_char(int e) {
    return e < 26 ? 'A' + (unsigned)e : e < 36 ? '0' + (unsigned)(e - 26) : 0;
}

/* Whether the results from from on already hold field off's text of r, case aside. */
static int ss_listed(int from, int off, void *r) {
    for (int i = from; i < ss.nmatch; ++i)
        if (!strcasecmp(P(ss.match[i], off), P(r, off))) return 1;
    return 0;
}

static int ss_has(const char *s) { return s && *s && strcasestr(s, ss.text); }

/* Two records by their field ss.by, case aside: a tab's artists or albums A to Z. */
static int ss_by_name(const void *a, const void *b) {
    return strcasecmp(P(*(void *const *)a, ss.by), P(*(void *const *)b, ss.by));
}

/* The tab's results, ASCII case aside (as the playlist editor's Add Songs search), none while the
 * text is empty, as on an iPod: the songs whose title, artist or album holds the text, then the
 * artists, then the albums, whose name holds it, one row per name. All lists SS_GROUP of each,
 * its artists and albums in the order their songs come; a group's own tab all of them, the
 * artists and albums A to Z (sorted, so a big library's thousand artists are not each checked
 * against the rest). Any song matching is any match at all: an artist's or album's song does. */
static void ss_filter(void) {
    int n = ss.songs ? (int)deque_size(ss.songs) : 0, all = ss.tab == SS_TAB_ALL;
    ss.nmatch = ss.sel = ss.top = ss.nsongs = ss.any = 0;
    if (!ss.text[0] || !ss.match) return;
    int songs = all ? SS_GROUP : ss.tab == SS_TAB_SONGS ? n : 0;
    for (int i = 0; i < n && (ss.nmatch < songs || !ss.any); ++i) {
        void *r = deque_at(ss.songs, (unsigned)i);
        char name[512];
        if (!ss_has(track_name(name, sizeof name, r)) && !ss_has(P(r, REC_ARTIST)) &&
            (ss.pick || !ss_has(P(r, REC_ALBUM)))) /* Add Songs: the title or artist alone */
            continue;
        ss.any = 1;
        if (ss.nmatch < songs) {
            ss.kind[ss.nmatch] = SS_SONG;
            ss.match[ss.nmatch++] = r;
        }
    }
    ss.nsongs = ss.nmatch;
    static const int field[] = { REC_ARTIST, REC_ALBUM };
    for (int g = 0; g < 2 && ss.any; ++g) {
        if (!all && ss.tab != SS_TAB_ARTISTS + g) continue;
        int from = ss.nmatch, most = all ? SS_GROUP : n;
        for (int i = 0; i < n && ss.nmatch - from < most; ++i) {
            void *r = deque_at(ss.songs, (unsigned)i);
            if (!ss_has(P(r, field[g])) || (all && ss_listed(from, field[g], r))) continue;
            ss.kind[ss.nmatch] = (unsigned char)(SS_ARTIST + g);
            ss.match[ss.nmatch++] = r;
        }
        if (all || ss.nmatch == from) continue;
        ss.by = field[g];
        qsort(ss.match + from, (unsigned)(ss.nmatch - from), sizeof *ss.match, ss_by_name);
        int to = from + 1;
        for (int i = from + 1; i < ss.nmatch; ++i)
            if (strcasecmp(P(ss.match[i], ss.by), P(ss.match[to - 1], ss.by)))
                ss.match[to++] = ss.match[i];
        ss.nmatch = to;
    }
}

/* Entry e typed at the cursor: a lower-case letter (Rename's a capital at a word's start), a
 * digit, a space (not first, nor beside another), or the character before the cursor taken off.
 * The text is ASCII, a byte a character. */
static void ss_type(int e) {
    int n = (int)strlen(ss.text), at = ss.cur;
    if (e == SS_DELETE) {
        if (!at) return;
        for (int i = at - 1; i < n; ++i) ss.text[i] = ss.text[i + 1];
        --ss.cur;
    } else {
        int cap = ss.rename && (!at || ss.text[at - 1] == ' ');
        char c = (char)(e == SS_SPACE ? ' '
                        : e < 26      ? (cap ? 'A' : 'a') + (unsigned)e
                                      : ss_char(e));
        if (n >= SS_TEXT_MAX || (c == ' ' && (!at || ss.text[at - 1] == ' ' || ss.text[at] == ' ')))
            return;
        for (int i = n + 1; i > at; --i) ss.text[i] = ss.text[i - 1];
        ss.text[at] = c;
        ++ss.cur;
    }
    if (!ss.rename) ss_filter();
}

/* The result at sel: a song plays, the songs found after it as the queue (a song list's class, as
 * Most Played's), which playing_page copies before it returns; an artist opens stock's artist page,
 * as Now Playing's Artist info does; an album its tracks in Cover Flow, as Era's albums do. Under
 * Rename, Done; under Add Songs, the song in or out of the playlist. */
extern int coverflow_goto_album(void *r);
static void ss_play(void) {
    if (ss.rename) { /* Done: the name, its edge spaces gone, saved */
        char name[SS_TEXT_MAX + 1];
        unsigned a = 0, b = strlen(ss.text);
        while (ss.text[a] == ' ') ++a;
        while (b > a && ss.text[b - 1] == ' ') --b;
        tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), ss.text + a);
        int r = *name ? playlist_rename(ss.rename - 1, name) : 0;
        toast(!*name    ? "Name can't be empty"
              : r == -2 ? "A playlist has that name"
              : r       ? "Renamed"
                        : "Could not rename playlist");
        if (r > 0) {
            g_delete_flag = 1; /* Playlists reads its rows again */
            navigator_back();
        }
        return;
    }
    if (ss.sel >= ss.nmatch) return;
    void *r = ss.match[ss.sel], *dq;
    if (ss.pick) { /* Add Songs: in or out of the playlist, its mark changed in place */
        add_songs_toggle(r);
        return;
    }
    if (ss.kind[ss.sel] == SS_ARTIST) {
        struct {
            int cls;
            void *r;
        } context = { 0xf001, r };
        navigator_to_with_context("localmusic/artistinfo_page", &context);
    } else if (ss.kind[ss.sel] == SS_ALBUM) {
        if (!coverflow_goto_album(r)) toast("Album not found");
    } else if ((dq = _create_deque("stSongInfo"))) {
        deque_init(dq);
        for (int i = 0; i < ss.nsongs; ++i) _deque_push_back(dq, ss.match[i]);
        play_folder(dq, ss.sel, 0xf001);
        deque_destroy(dq);
    }
}

/* The pop's box on the sheet: the bubble's, whose key sits on the lit cell's accent box. */
static rect_t ss_pop_box(void) {
    return (rect_t){ SS_LIT_X - (SS_POP_W - SS_POP_KEY_W) / 2,
                     SS_STRIP_Y + SS_BOX_IN + SS_POP_KEY_H - SS_POP_H, SS_POP_W, SS_POP_H };
}
static int ss_popping(void) { return ss.pop == 1 && !ss.focus && ss.letter < SS_SPACE; }
static void ss_pop_dirty(void) {
    rect_t r = ss_pop_box();
    if (ss.sheet) widget_invalidate_force(ss.sheet, &r); /* the bubble's box alone */
}
/* A spin has stopped: its one pop; a pop's time is up: gone. */
static int ss_pop_due(const void *unused) {
    ss.pop_timer = 0;
    if (ss.pop == 2) {
        ss.pop = 1;
        ss.pop_timer = timer_add(ss_pop_due, (void *)0, SS_POP_MS);
    } else
        ss.pop = 0;
    ss_pop_dirty();
    return 0;
}
/* A step of the strip's letters, by the wheel or a drag (or a tap's letter): the pop at once, or,
 * inside a spin, once it stops. */
static void ss_pop_step(void) {
    unsigned now = (unsigned)time_now_ms();
    int spin = ss.pop_step_at && now - ss.pop_step_at < SS_POP_FAST_MS;
    ss.pop_step_at = now | 1;
    ss.pop = spin ? 2 : 1;
    rearm(&ss.pop_timer, ss_pop_due, spin ? SS_POP_FAST_MS : SS_POP_MS);
}

/* Cell i of the image name, w by h, at x, y. */
static void ss_cell(void *canvas, const char *name, int i, int w, int h, int x, int y) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (!ss.sheet || widget_load_image(ss.sheet, theme_art(name), bitmap)) return;
    rect_t src = { i * w, 0, w, h }, dst = { x, y, w, h };
    canvas_draw_image(canvas, bitmap, &src, &dst);
}
/* The bubble over the lit cell, the big letter in its head, the echo on its key. */
static void ss_pop_paint(void *canvas) {
    rect_t b = ss_pop_box();
    ss_cell(canvas, "search_pop", 0, SS_POP_W, SS_POP_H, b.x, b.y);
    ss_cell(canvas, "search_pop_letters", ss.letter, SS_POP_HEAD, SS_POP_HEAD, b.x, b.y);
    ss_cell(canvas, "search_pop_echoes", ss.letter, SS_POP_KEY_W, SS_POP_ECHO_H, SS_LIT_X,
            SS_STRIP_Y);
}

/* Whether the tabs show: while the wheel is off the strip, never under Rename or Add Songs. The
 * rows showing, and the first one's top: under the tabs while they show. */
static int ss_tabs(void) { return ss.focus && !ss.rename && !ss.pick; }
static int ss_rows(void) { return ss_tabs() ? SS_TAB_ROWS : SS_ROWS; }
static int ss_list_y(void) { return ss_tabs() ? SS_LIST_Y : 0; }
/* The bar's row kept in view. */
static void ss_keep(void) {
    if (ss.sel < ss.top) ss.top = ss.sel;
    if (ss.sel >= ss.top + ss_rows()) ss.top = ss.sel - ss_rows() + 1;
}
static void ss_pick(int tab) {
    ss.tab = (tab + SS_TABS) % SS_TABS;
    ss_filter();
}

/* ringnav: the page's keys before stock's (Play/Pause and a held Return's release go on). */
int search_strip_key(void *top, unsigned key) {
    if (!ss.page || top != ss.page) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    int wheel = key == KEY_NEXT   ? 1
                : key == KEY_PREV ? -1
                                  : 0,
        button = key == KEY_FWD_BTN    ? 1
                 : key == KEY_BACK_BTN ? -1
                                       : 0;
    if (ss.focus == SS_ON_LIST && (button < 0 || (wheel < 0 && !ss.sel)))
        ss.focus = button || ss.rename || ss.pick ? SS_ON_STRIP : SS_ON_TABS, wheel = button = 0,
        key = 0;
    if (ss.focus && key && !wheel && !button && key != KEY_CENTER && key != KEY_RETURN)
        return 0; /* Play/Pause, on the results too */
    if (ss.focus == SS_ON_TABS) {
        if (wheel < 0 || key == KEY_RETURN)
            ss.focus = SS_ON_STRIP;
        else if (wheel > 0 && ss.nmatch)
            ss.focus = SS_ON_LIST, ss.sel = ss.top;
        else if (key == KEY_CENTER || button)
            ss_pick(ss.tab + (button < 0 ? -1 : 1));
    } else if (ss.focus == SS_ON_LIST) {
        if (wheel && ss.sel + wheel >= 0 && ss.sel + wheel < ss.nmatch) ss.sel += wheel;
        if (key == KEY_CENTER) ss_play();
        if (key == KEY_RETURN) ss.focus = SS_ON_STRIP;
    } else if (!key) {
        /* the results' Previous, back on the strip */
    } else if (wheel) {
        ss.letter = (ss.letter + wheel + SS_ENTRIES) % SS_ENTRIES;
        ss_pop_step();
    } else if (key == KEY_CENTER)
        ss_type(ss.letter);
    else if (button < 0) {
        if (ss.cur) --ss.cur;
    } else if (button > 0 && ss.text[ss.cur])
        ++ss.cur;
    else if (button > 0) {
        if (ss.rename || (ss.pick && ss.nmatch)) /* Rename's Done, Add Songs' songs */
            ss.focus = SS_ON_LIST;
        else if (ss.any && !ss.pick)
            ss.focus = ss.nmatch ? SS_ON_LIST : SS_ON_TABS; /* an empty tab: the tabs */
    } else if (key == KEY_RETURN)
        ss_type(SS_DELETE);
    else
        return 0;
    ss_keep();
    if (ss.sheet) widget_invalidate_force(ss.sheet, (void *)0);
    /* stock's key-up clears the click latch, and ringnav skips it: the next press clicks */
    *(volatile signed char *)KEY_TONE_LATCH = 0;
    return 1;
}

/* Hold Return (compact_now_playing) on the page: Search closes, whatever has the wheel. The latch
 * stays armed for stock's filter to swallow the release, as on the hold's other ways. */
int search_strip_hold(void *top) {
    if (!ss.page || top != ss.page) return 0;
    navigator_back();
    return 1;
}

/* UTF-8 s as wide characters in out (at most max - 1 of them), 0-ended; its length. */
static unsigned ss_wide(const char *s, unsigned *out, unsigned max) {
    unsigned n = 0;
    for (const unsigned char *p = (const unsigned char *)s; p && *p && n + 1 < max; ++n) {
        unsigned c = *p++;
        if (c >= 0xc0) {
            int k = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : 1;
            for (c &= 0x3f >> k; k-- && (*p & 0xc0) == 0x80;) c = c << 6 | (*p++ & 63);
        }
        out[n] = c;
    }
    out[n] = 0;
    return n;
}

/* Text s in face at px, left-aligned from x and centred on the box's rows, ending in an ellipsis
 * where it is wider than room, in rgb (opaque unless it gives an alpha). Returns the width drawn.
 */
static int ss_text(void *canvas, const char *face, const char *s, int x, int y, int h, int room,
                   int px, unsigned rgb) {
    unsigned wide[96];
    unsigned n = ss_wide(s, wide, sizeof wide / sizeof *wide - 1);
    int fpx = px;
    if (!face) face = theme_face(wide, (int)n, &fpx);
    canvas_set_font(canvas, face, (unsigned)fpx);
    int w = (int)(canvas_measure_text(canvas, wide, n) + 0.5f);
    if (w > room) {
        wide[n] = 0x2026;
        while (n && (w = (int)(canvas_measure_text(canvas, wide, n + 1) + 0.5f)) > room) {
            wide[n - 1] = 0x2026;
            --n;
        }
        wide[++n] = 0;
    }
    rect_t r = { x, y, w + 2, h };
    final_text(canvas, face, wide, &r, (unsigned)fpx, rgb >> 24 ? rgb : 0xff000000u | rgb);
    return w;
}

/* The typed text from x in room, scrolled to keep the cursor in it: from the first character that
 * leaves the cursor inside room, up to the last that fits. Returns the cursor's x from x. */
static int ss_typed(void *canvas, int x, int y, int h, int room, unsigned rgb) {
    unsigned wide[SS_TEXT_MAX + 1];
    unsigned n = ss_wide(ss.text, wide, sizeof wide / sizeof *wide), at = 0, end = n,
             cur = (unsigned)ss.cur;
    int px = SS_PX_TYPED;
    const char *face = theme_face(wide, (int)n, &px);
    canvas_set_font(canvas, face, (unsigned)px);
#define SS_W(a, b)                                                                                 \
    ((b) > (a) ? (int)(canvas_measure_text(canvas, wide + (a), (b) - (a)) + 0.5f) : 0)
    while (at < cur && SS_W(at, cur) > room) ++at;
    while (end > cur && SS_W(at, end) > room) --end;
    int cw = SS_W(at, cur), w = SS_W(at, end);
#undef SS_W
    wide[end] = 0;
    rect_t r = { x, y, w + 2, h };
    if (end > at) final_text(canvas, face, wide + at, &r, (unsigned)px, 0xff000000u | rgb);
    return cw;
}

/* ringnav_paint: the results and the strip, on the sheet. Colours are the theme's tokens
 * (docs/oled-design.md#colour), drawn as stated in each half. */
void search_strip_paint(void *w, void *canvas) {
    if (!ss.page || w != ss.sheet || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int light = ipod_light();
    unsigned primary = light ? 0x000000 : 0xffffff, secondary = light ? 0x5c5c5c : 0xababab,
             tertiary = light ? 0x6e6e6e : 0x858585, accent = accent_tone(2);
    if (ss.rename) { /* Rename's one row: Done, the accent's, white when lit */
        int on = ss.focus == SS_ON_LIST;
        if (on) fill_box(canvas, &(rect_t){ 16, 0, 375 - 32, SS_PITCH }, RGBA(accent), 8);
        ss_text(canvas, 0, "Done", 24, 0, SS_PITCH, SS_TITLE_W, SS_PX_ROW, on ? 0xffffff : accent);
    }
    if (ss_tabs()) {
        static const char *const tabs[] = { "All", "Songs", "Artists", "Albums" };
        segments_paint(canvas, tabs, SS_TABS, ss.tab, ss.focus == SS_ON_TABS, SS_TABS_X, SS_TABS_Y,
                       SS_TABS_W, SS_TABS_H);
    }
    for (int i = 0; i < ss_rows() && ss.top + i < ss.nmatch && !ss.rename; ++i) {
        void *r = ss.match[ss.top + i];
        int y = ss_list_y() + i * SS_PITCH, on = ss.focus == SS_ON_LIST && ss.top + i == ss.sel;
        char name[512];
        if (on) fill_box(canvas, &(rect_t){ 16, y, 375 - 32, SS_PITCH }, RGBA(accent), 8);
        int kind = ss.kind[ss.top + i];
        const char *title = kind == SS_ARTIST  ? P(r, REC_ARTIST)
                            : kind == SS_ALBUM ? P(r, REC_ALBUM)
                                               : track_name(name, sizeof name, r);
        /* a row that can't play now (docs/server-library.md#greyed-rows): a server song offline,
         * an artist or album none of whose songs can play; its text CAT_GREY, on the bar the
         * bar's white at 70%, and the offline mark at its right, the text ending clear of it */
        int grey = kind == SS_ARTIST  ? catalogue_artist_unavailable(r)
                   : kind == SS_ALBUM ? catalogue_album_unavailable(r)
                                      : catalogue_unavailable(r),
            right = grey ? SS_MARK_X - CAT_MARK_W / 2 - SS_MARK_GAP : SS_RIGHT;
        unsigned ink = on ? (grey ? 0xb3ffffffu : 0xffffffffu)
                          : 0xff000000u | (grey ? 0x666666 : primary),
                 note = grey || on ? ink : 0xff000000u | tertiary;
        int tw = ss_text(canvas, 0, title, 24, y, SS_PITCH,
                         SS_TITLE_W < right - 24 ? SS_TITLE_W : right - 24, SS_PX_ROW, ink);
        /* beside it, what the row is, as an iPod's result icons say: "Artist", "Album" and its
         * artist, or a song's artist */
        char what[300];
        const char *artist = P(r, REC_ARTIST);
        if (kind == SS_ARTIST)
            artist = "Artist";
        else if (kind == SS_ALBUM) {
            tk_snprintf(what, sizeof what, artist && *artist ? "Album \xc2\xb7 %s" : "Album",
                        artist);
            artist = what;
        }
        int ax = 24 + tw + 10, mark = ss.pick ? add_songs_mark(r) : 0;
        if (mark) { /* Add Songs' mark on the right column (playlist_pages.c pv_paint's) */
            static const char *const kinds[] = { "in", "add", "out" };
            char art[32];
            tk_snprintf(art, sizeof art, "plmark_%s_%s", kinds[mark - 1],
                        on      ? "w"
                        : light ? "l"
                                : "d");
            paint_art(ss.sheet, canvas, theme_art(art), SS_RIGHT - 21, y + (SS_PITCH - 20) / 2);
            right -= 30;
        }
        if (artist && *artist && right - ax >= SS_ARTIST_MIN)
            ss_text(canvas, 0, artist, ax, y + 1, SS_PITCH, right - ax, SS_PX_ARTIST, note);
        if (grey) catalogue_mark_paint(canvas, SS_MARK_X, y + SS_PITCH / 2, ink);
    }
    if (ss.text[0] && !ss.nmatch && !ss.rename) {
        int px = SS_PX_NOTE;
        static const unsigned none[] = { 'N', 'o', ' ', 'r', 'e', 's', 'u', 'l', 't', 's', 0 };
        rect_t r = { 0, ss_list_y(), 375, SS_STRIP_Y - ss_list_y() };
        final_text(canvas, theme_face(none, 10, &px), none, &r, (unsigned)px,
                   0xff000000u | secondary);
    }
    /* the strip: a #1C1C1E pill, the text in a black box with its cursor, the letters in
     * equal cells about the lit one, white on the accent while the strip has the wheel */
    int y0 = SS_STRIP_Y, cy = y0 + SS_STRIP_H / 2;
    unsigned strip = flip_if(light, 0x1c1c1e);
    fill_box(canvas, &(rect_t){ SS_STRIP_X, y0, 375 - 2 * SS_STRIP_X, SS_STRIP_H }, RGBA(strip),
             SS_STRIP_R);
    fill_box(canvas,
             &(rect_t){ SS_BOX_X, y0 + SS_BOX_IN, SS_BOX_R - SS_BOX_X, SS_STRIP_H - 2 * SS_BOX_IN },
             RGBA(light ? 0xffffff : 0), SS_BOX_RADIUS);
    int cx = ss_typed(canvas, SS_TYPED_X, y0, SS_STRIP_H, SS_BOX_R - 6 - SS_TYPED_X - 1, primary);
    if (!ss.focus) { /* the cursor, steady, in the text's ink */
        stock_canvas_set_fill_color(canvas, RGBA(primary));
        canvas_fill_rect(canvas, SS_TYPED_X + 1 + cx, cy - 9, 2, 18);
    }
    for (int k = -SS_SIDE; k <= SS_SIDE; ++k) {
        int e = (ss.letter + k + SS_ENTRIES) % SS_ENTRIES, on = !k, x = SS_LIT_X + k * SS_STEP;
        unsigned ink = on && !ss.focus ? 0xffffff : on ? primary : secondary;
        if (on && ss_popping()) continue; /* ss_pop_paint draws the cell */
        if (on && !ss.focus)
            fill_box(canvas,
                     &(rect_t){ x, y0 + SS_BOX_IN, SS_POP_KEY_W, SS_STRIP_H - 2 * SS_BOX_IN },
                     RGBA(accent), 5);
        unsigned c[2] = { ss_char(e), 0 };
        int gx = x + 2; /* the space and delete marks, 11 px wide in the cell */
        stock_canvas_set_fill_color(canvas, RGBA(ink));
        if (e == SS_SPACE) { /* an open box under the line, the keyboard's space */
            canvas_fill_rect(canvas, gx + 2, cy + 4, 11, 2);
            canvas_fill_rect(canvas, gx + 2, cy, 2, 4);
            canvas_fill_rect(canvas, gx + 11, cy, 2, 4);
        } else if (e == SS_DELETE) { /* a left-pointing arrow */
            canvas_fill_rect(canvas, gx + 2, cy - 1, 11, 2);
            for (int d = 1; d <= 4; ++d) {
                canvas_fill_rect(canvas, gx + 2 + d, cy - 1 - d, 2, 1);
                canvas_fill_rect(canvas, gx + 2 + d, cy + d, 2, 1);
            }
        } else {
            rect_t r = { x, y0, SS_STEP, SS_STRIP_H };
            int px = SS_PX_LETTER;
            final_text(canvas, theme_face(c, 1, &px), c, &r, (unsigned)px, 0xff000000u | ink);
        }
    }
    /* the run's ends dissolve into the strip: a column of its grey over each, opaque at the edge,
     * as color_t lays it out (RGBA), the pill's own fill exactly */
    for (int i = 0; i < SS_FADE; ++i) {
        unsigned a = (unsigned)(255 * (SS_FADE - i) / SS_FADE);
        stock_canvas_set_fill_color(canvas, a << 24 | (RGBA(strip) & 0xffffffu));
        canvas_fill_rect(canvas, SS_RUN_L + i, y0 + 6, 1, SS_STRIP_H - 12);
        canvas_fill_rect(canvas, SS_RUN_R - 1 - i, y0 + 6, 1, SS_STRIP_H - 12);
    }
    if (ss_popping()) ss_pop_paint(canvas);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}

#define SS_TAP 8 /* a touch that moves less than this is a tap */
static int ss_dist(int d) { return d < 0 ? -d : d; }

/* The strip's letters moved by n cells, a pop for the step, as the wheel's. */
static void ss_slide(int n) {
    if (!n) return;
    ss.letter = ((ss.letter + n) % SS_ENTRIES + SS_ENTRIES) % SS_ENTRIES;
    ss_pop_step();
}

/* The sheet's press, and the page's moves and release wherever they land (as 3EQ's sliders take
 * theirs). A drag on the strip moves its letters as it goes: left brings later letters under the
 * highlight. */
static int ss_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) }, type = I(event, EVENT_TYPE);
    if (!ss.sheet) return 0;
    widget_to_local(ss.sheet, xy);
    if (type == EVT_POINTER_DOWN) {
        ss.down_x = (short)xy[0], ss.down_y = (short)xy[1], ss.pressed = 1, ss.swiped = 0;
        return 0;
    }
    if (!ss.pressed) return 0;
    int dx = xy[0] - ss.down_x, dy = xy[1] - ss.down_y,
        tap = ss_dist(dx) < SS_TAP && ss_dist(dy) < SS_TAP, strip = ss.down_y >= SS_STRIP_Y;
    if (strip && !tap) {
        int cells = -dx / SS_STEP;
        ss_slide(cells - ss.swiped);
        ss.swiped = (short)cells;
        ss.focus = SS_ON_STRIP;
    }
    if (type == EVT_POINTER_MOVE_BEFORE) {
        if (strip && !tap) widget_invalidate_force(ss.sheet, (void *)0);
        return 0;
    }
    ss.pressed = 0;
    if (strip) {
        int k = (ss.down_x - SS_LIT_X + SS_SIDE * SS_STEP + SS_STEP) / SS_STEP - SS_SIDE - 1;
        k = k < -SS_SIDE ? -SS_SIDE : k > SS_SIDE ? SS_SIDE : k; /* the run's faded ends */
        ss.focus = SS_ON_STRIP;
        if (tap && ss.down_x >= SS_BOX_R && ss.down_x < 375 - SS_STRIP_X) {
            ss_slide(k);
            if (!k) ss_pop_step();
            ss_type(ss.letter);
        }
    } else if (!tap) {
        int most = ss.nmatch > ss_rows() ? ss.nmatch - ss_rows() : 0;
        ss.top -= dy / SS_PITCH;
        ss.top = ss.top < 0 ? 0 : ss.top > most ? most : ss.top;
        if (ss.sel < ss.top) ss.sel = ss.top;
        if (ss.sel >= ss.top + ss_rows()) ss.sel = ss.top + ss_rows() - 1;
    } else if (ss_tabs() && ss.down_y < SS_LIST_Y) {
        int t = (ss.down_x - SS_TABS_X) * SS_TABS / SS_TABS_W;
        ss.focus = SS_ON_TABS;
        ss_pick(t < 0 ? 0 : t >= SS_TABS ? SS_TABS - 1 : t);
    } else {
        int y = ss.down_y - ss_list_y(), row = y / SS_PITCH;
        if (y >= 0 && row < ss_rows() && ss.top + row < ss.nmatch) {
            ss.focus = SS_ON_LIST;
            ss.sel = ss.top + row;
            ss_keep();
            ss_play();
        }
    }
    ss_keep();
    widget_invalidate_force(ss.sheet, (void *)0);
    return 0;
}

static int ss_closed(void *ctx, void *event) {
    stop_timer(&ss.pop_timer);
    if (ss.songs) deque_destroy(ss.songs);
    free(ss.match);
    free(ss.kind);
    memset(&ss, 0, sizeof ss);
    return 0;
}

/* The page and its sheet, titled caption on the status row. */
static void ss_sheet(const char *caption) {
    void *title = text(ss.page, 0, 0, 375, 28); /* what the status row reads */
    widget_set_text_utf8(title, caption);
    widget_set_name(title, PAGE_TITLE);
    widget_set_visible(title, 0, 0);
    ss.sheet = widget_factory_create_widget(widget_factory(), "view", ss.page, 0, 0, 375,
                                            widget_get_prop_int(ss.page, "h", client_h()));
    widget_on(ss.sheet, EVT_POINTER_DOWN, ss_touch, 0);
    widget_on(ss.page, EVT_POINTER_MOVE_BEFORE, ss_touch, 0);
    widget_on(ss.page, EVT_POINTER_UP_BEFORE, ss_touch, 0);
}

/* The Library's Search row and Local Songs' pull: the strip under a list.entry.ipod theme, stock's
 * search dialog under the others. */
int library_search_open(void *ctx, void *event) {
    if (!theme()->entry_ipod) return stock_search(ctx, event);
    if (ss.page || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup))) return 0;
    int n;
    ss.songs = staged_music(all_songs, (void *)LIBRARY_MUSIC, &n);
    n = ss.songs ? (int)deque_size(ss.songs) : 0;
    ss.match = n ? calloc((unsigned)n + 2 * SS_GROUP, sizeof *ss.match) : (void *)0;
    ss.kind = ss.match ? calloc((unsigned)n + 2 * SS_GROUP, 1) : (void *)0;
    if (!ss.kind) free(ss.match), ss.match = (void *)0;
    ss_sheet("Search");
    return 0;
}

/* A playlist's Rename (its hold menu, docs/oled-design.md#playlists): the strip with the name in
 * it, the cursor at its end (a word's first letter a capital), and one row above, Done, which Next
 * at the text's end moves to as Search's results and Centre saves (playlists.c playlist_rename);
 * a held Return closes the page and leaves the name as it was. */
void playlist_rename_open(int id, const char *name) {
    if (ss.page || id < 0 || id >= 100 ||
        !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup)))
        return;
    ss.rename = id + 1;
    ss.nmatch = 1; /* Done */
    tk_snprintf(ss.text, sizeof ss.text, "%s", name ? name : "");
    ss.cur = (int)strlen(ss.text);
    ss_sheet("Rename");
}

/* Add Songs' Search row (add_songs.c): the strip over the library's songs alone, each found by its
 * title or artist, with its mark; Centre on a result adds it or takes it out, and the results
 * stay. */
void add_songs_search(void) {
    library_search_open(0, 0);
    ss.pick = ss.page != 0;
    ss.tab = SS_TAB_SONGS;
}
