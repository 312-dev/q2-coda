/* Search as an iPod classic's letter strip (list.entry.ipod, the OLED theme's;
 * docs/ipod.md#search): the Library's Search row opens a page with a bar along the bottom, the
 * typed text at its left and the alphabet passing under a fixed highlight, and what matches in the
 * list above it, filtered as each character goes in: the artists, then the albums, then the songs,
 * as an iPod's results mix them.
 *
 * Keys, as Apple's iPod classic guide gives them: the wheel walks the strip (A to Z, 0 to 9, a
 * space, then delete, round again), Centre enters what is highlighted, Next enters a space and
 * Previous deletes the last character. Return, as the iPod's Menu, shows the results list to
 * navigate: the wheel moves its bar, Centre opens an artist (stock's artist page) or an album (its
 * tracks in Cover Flow) or plays a song, the songs after it as the queue. A turn
 * up from the first result, Next or Previous goes back to the strip; Return there closes Search,
 * as does Return on the strip with no text or no match. Play/Pause stays the player's.
 *
 * The key pop (ss_pop_paint): when the wheel lands on a letter or digit, its key grows a bubble
 * holding the letter large, and the key keeps it at the strip's size, dimmed; it goes SS_POP_MS
 * after the wheel stops. A spin faster than SS_POP_FAST_MS a step shows none until it stops, then
 * one. The bubble and the letters are tools/art.py's images (pop_images), drawn at whole pixels.
 *
 * Touch (ss_touch): a tap on a letter types it, a drag along the strip moves the alphabet a cell
 * per 17 px, a tap on a result opens or plays it, and a drag up or down the results scrolls them a
 * row per 32 px.
 *
 * The page is one sheet painted here (search_strip_paint), not rows: the strip and the list share
 * the wheel, and only one of them shows a selection at a time. */
#include "navigation.h"

#define SS_ROWS 4 /* result rows above the strip, 32 px each from the list's top */
#define SS_PITCH 32
#define SS_STRIP_Y 158   /* the strip's top on the page, under the 58 px status row: y 216 */
#define SS_STRIP_H 38    /* ... to y 254 */
#define SS_BOX_R 98      /* the typed text's black box ends here; the letters start 10 px on */
#define SS_STEP 17       /* a letter's cell */
#define SS_SLOTS 14      /* cells across the strip */
#define SS_HIGH 3        /* the cell under the highlight */
#define SS_TEXT_MAX 48   /* characters typed, at most */
#define SS_TITLE_W 233   /* a result's title, at most; the artist takes the rest */
#define SS_ARTIST_MIN 40 /* an artist with less room than this is left out */
#define SS_RIGHT 347     /* the right column */
#define SS_ENTRIES 38    /* 26 letters, 10 digits, the space and delete */
#define SS_SPACE 36
#define SS_DELETE 37
/* AWTK sizes a face by its line, 1.21 em for Inter: the design's 20, 18, 17, 16 and 15 px */
#define SS_PX_ROW 24
#define SS_PX_TYPED 22
#define SS_PX_LETTER 21
#define SS_PX_NOTE 19
#define SS_PX_ARTIST 18
/* The key pop (tools/art.py POP_*): the bubble's image is the head's width, its foot the key's,
 * the key 18 px wide from the lit slot's x - 2 and 7 px below the strip's top; the big letters and
 * the echoes are strips of a cell per strip character, a head's and a key's box each */
#define SS_POP_W 30
#define SS_POP_H 66
#define SS_POP_HEAD 30
#define SS_POP_KEY_W 18
#define SS_POP_KEY_H 24
#define SS_POP_ECHO_H 38
#define SS_POP_MS 300 /* the pop goes this long after the wheel's last step */
#define SS_POP_FAST_MS                                                                             \
    125 /* steps closer than this, 8 a second, are a spin: no pop until it stops */
/* artists, and albums, listed at most: each is checked against those before it */
#define SS_GROUP 32
enum { SS_SONG, SS_ARTIST, SS_ALBUM };

static struct {
    void *page, *sheet, *songs; /* the page, what it paints on, the library's songs A to Z */
    void **match;               /* the results: artists, albums, songs; a record of each */
    unsigned char *kind;        /* ... each one's SS_* */
    int nmatch, letter, focus, sel, top; /* focus 1: the results list has the wheel */
    int songs_at;                        /* the first song's place in match */
    short down_x, down_y, pressed;       /* a touch: where it went down on the sheet */
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

/* The artists, then the albums, whose name holds the text (one row each, SS_GROUP at most), then
 * the songs whose title, artist or album holds it, ASCII case aside (as the playlist editor's Add
 * Songs search); none while the text is empty, as on an iPod. */
static void ss_filter(void) {
    int n = ss.songs ? (int)deque_size(ss.songs) : 0;
    ss.nmatch = ss.sel = ss.top = ss.songs_at = 0;
    if (!ss.text[0] || !ss.match) return;
    static const int group[] = { REC_ARTIST, REC_ALBUM };
    for (int g = 0; g < 2; ++g)
        for (int i = 0, from = ss.nmatch; i < n && ss.nmatch - from < SS_GROUP; ++i) {
            void *r = deque_at(ss.songs, (unsigned)i);
            const char *name = P(r, group[g]);
            if (name && *name && strcasestr(name, ss.text) && !ss_listed(from, group[g], r)) {
                ss.kind[ss.nmatch] = g ? SS_ALBUM : SS_ARTIST;
                ss.match[ss.nmatch++] = r;
            }
        }
    ss.songs_at = ss.nmatch;
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(ss.songs, (unsigned)i);
        char name[512];
        const char *fields[] = { track_name(name, sizeof name, r), P(r, REC_ARTIST),
                                 P(r, REC_ALBUM) };
        for (unsigned f = 0; f < sizeof fields / sizeof *fields; ++f)
            if (fields[f] && strcasestr(fields[f], ss.text)) {
                ss.kind[ss.nmatch] = SS_SONG;
                ss.match[ss.nmatch++] = r;
                break;
            }
    }
}

/* Entry e typed: a lower-case letter, a digit, a space (not first, nor after another), or the
 * last character taken off. */
static void ss_type(int e) {
    unsigned n = strlen(ss.text);
    if (e == SS_DELETE) {
        if (!n) return;
        ss.text[n - 1] = 0;
    } else {
        unsigned c = e == SS_SPACE ? ' ' : e < 26 ? 'a' + (unsigned)e : ss_char(e);
        if (n >= SS_TEXT_MAX || (c == ' ' && (!n || ss.text[n - 1] == ' '))) return;
        ss.text[n] = (char)c;
        ss.text[n + 1] = 0;
    }
    ss_filter();
}

/* The result at sel: an artist opens stock's artist page, as Now Playing's Artist info does; an
 * album its tracks in Cover Flow, as Era's albums do; a song plays, the songs found after it as
 * the queue (a song list's class, as Most Played's), which playing_page copies before it returns.
 */
extern int coverflow_goto_album(void *r);
static void ss_play(void) {
    if (ss.sel >= ss.nmatch) return;
    void *r = ss.match[ss.sel], *dq;
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
        for (int i = ss.songs_at; i < ss.nmatch; ++i) _deque_push_back(dq, ss.match[i]);
        play_folder(dq, ss.sel - ss.songs_at, 0xf001);
        deque_destroy(dq);
    }
}

/* The pop's box on the sheet: the bubble's, whose key sits on the lit slot (x - 2 of the slot at
 * SS_HIGH, whole pixels, as tools/art.py's key is 18 px about the slot's centre). */
static rect_t ss_pop_box(void) {
    int key_left = SS_BOX_R + 10 + SS_HIGH * SS_STEP - 2;
    return (rect_t){ key_left - (SS_POP_W - SS_POP_KEY_W) / 2,
                     SS_STRIP_Y + 7 + SS_POP_KEY_H - SS_POP_H, SS_POP_W, SS_POP_H };
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
/* A wheel step on the strip: the pop at once, or, inside a spin, once it stops. */
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
/* The bubble over the lit slot, the big letter in its head, the echo on its key. */
static void ss_pop_paint(void *canvas) {
    rect_t b = ss_pop_box();
    ss_cell(canvas, "search_pop", 0, SS_POP_W, SS_POP_H, b.x, b.y);
    ss_cell(canvas, "search_pop_letters", ss.letter, SS_POP_HEAD, SS_POP_HEAD, b.x, b.y);
    ss_cell(canvas, "search_pop_echoes", ss.letter, SS_POP_KEY_W, SS_POP_ECHO_H,
            b.x + (SS_POP_W - SS_POP_KEY_W) / 2, SS_STRIP_Y);
}

/* ringnav: the page's keys before stock's (Play/Pause and a held Return's release go on). */
int search_strip_key(void *top, unsigned key) {
    if (!ss.page || top != ss.page) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (ss.focus && (key == KEY_FWD_BTN || key == KEY_BACK_BTN || (wheel < 0 && !ss.sel)))
        ss.focus = 0, wheel = 0; /* back to the strip; a button still types */
    if (ss.focus && !wheel && key != KEY_CENTER && key != KEY_RETURN)
        return 0; /* Play/Pause, on the results too */
    if (ss.focus) {
        if (wheel && ss.sel + wheel >= 0 && ss.sel + wheel < ss.nmatch) ss.sel += wheel;
        if (ss.sel < ss.top) ss.top = ss.sel;
        if (ss.sel >= ss.top + SS_ROWS) ss.top = ss.sel - SS_ROWS + 1;
        if (key == KEY_CENTER) ss_play();
        if (key == KEY_RETURN) navigator_back();
    } else if (wheel) {
        ss.letter = (ss.letter + wheel + SS_ENTRIES) % SS_ENTRIES;
        ss_pop_step();
    } else if (key == KEY_CENTER)
        ss_type(ss.letter);
    else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
        ss_type(key == KEY_FWD_BTN ? SS_SPACE : SS_DELETE);
    else if (key == KEY_RETURN && ss.nmatch)
        ss.focus = 1;
    else if (key == KEY_RETURN)
        navigator_back();
    else if (key != KEY_PREV && key != KEY_NEXT)
        return 0;
    if (ss.sheet) widget_invalidate_force(ss.sheet, (void *)0);
    /* stock's key-up clears the click latch, and ringnav skips it: the next press clicks */
    *(volatile signed char *)KEY_TONE_LATCH = 0;
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
 * where it is wider than room. Returns the width drawn. */
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
    final_text(canvas, face, wide, &r, (unsigned)fpx, 0xff000000u | rgb);
    return w;
}

/* The typed text's tail that fits room, from x; its width. */
static int ss_typed(void *canvas, int x, int y, int h, int room, unsigned rgb) {
    unsigned wide[SS_TEXT_MAX + 1];
    unsigned n = ss_wide(ss.text, wide, sizeof wide / sizeof *wide), at = 0;
    int px = SS_PX_TYPED;
    const char *face = theme_face(wide, (int)n, &px);
    canvas_set_font(canvas, face, (unsigned)px);
    int w = n ? (int)(canvas_measure_text(canvas, wide, n) + 0.5f) : 0;
    while (at < n && w > room) {
        ++at;
        w = (int)(canvas_measure_text(canvas, wide + at, n - at) + 0.5f);
    }
    rect_t r = { x, y, w + 2, h };
    if (at < n) final_text(canvas, face, wide + at, &r, (unsigned)px, 0xff000000u | rgb);
    return w;
}

/* ringnav_paint: the results and the strip, on the sheet. Colours are the theme's tokens
 * (docs/oled-design.md#colour), drawn as stated in each half. */
void search_strip_paint(void *w, void *canvas) {
    if (!ss.page || w != ss.sheet || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int light = ipod_light();
    unsigned primary = light ? 0x000000 : 0xffffff, secondary = light ? 0x5c5c5c : 0xababab,
             tertiary = light ? 0x6e6e6e : 0x858585, accent = accent_tone(2);
    for (int i = 0; i < SS_ROWS && ss.top + i < ss.nmatch; ++i) {
        void *r = ss.match[ss.top + i];
        int y = i * SS_PITCH, on = ss.focus && ss.top + i == ss.sel;
        char name[512];
        if (on) fill_box(canvas, &(rect_t){ 16, y, 375 - 32, SS_PITCH }, RGBA(accent), 8);
        int kind = ss.kind[ss.top + i];
        const char *title = kind == SS_ARTIST  ? P(r, REC_ARTIST)
                            : kind == SS_ALBUM ? P(r, REC_ALBUM)
                                               : track_name(name, sizeof name, r);
        int tw = ss_text(canvas, 0, title, 24, y, SS_PITCH, SS_TITLE_W, SS_PX_ROW,
                         on ? 0xffffff : primary);
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
        int ax = 24 + tw + 10;
        if (artist && *artist && SS_RIGHT - ax >= SS_ARTIST_MIN)
            ss_text(canvas, 0, artist, ax, y + 1, SS_PITCH, SS_RIGHT - ax, SS_PX_ARTIST,
                    on ? 0xffffff : tertiary);
    }
    if (ss.text[0] && !ss.nmatch) {
        int px = SS_PX_NOTE;
        static const unsigned none[] = { 'N', 'o', ' ', 'r', 'e', 's', 'u', 'l', 't', 's', 0 };
        rect_t r = { 0, 0, 375, SS_STRIP_Y };
        final_text(canvas, theme_face(none, 10, &px), none, &r, (unsigned)px,
                   0xff000000u | secondary);
    }
    /* the strip: #1C1C1E, 12 px corners, the text in a black box with the accent's caret */
    int y0 = SS_STRIP_Y, cy = y0 + SS_STRIP_H / 2;
    fill_box(canvas, &(rect_t){ 16, y0, 375 - 32, SS_STRIP_H }, RGBA(flip_if(light, 0x1c1c1e)), 12);
    fill_box(canvas, &(rect_t){ 22, y0 + 6, SS_BOX_R - 22, SS_STRIP_H - 12 },
             RGBA(light ? 0xffffff : 0), 8);
    int tw = ss_typed(canvas, 30, y0, SS_STRIP_H, SS_BOX_R - 6 - 31, primary);
    if (!ss.focus) {
        stock_canvas_set_fill_color(canvas, RGBA(accent));
        canvas_fill_rect(canvas, 31 + tw, cy - 9, 2, 18);
    }
    for (int k = 0, x = SS_BOX_R + 10; k < SS_SLOTS; ++k, x += SS_STEP) {
        int e = (ss.letter - SS_HIGH + k + 2 * SS_ENTRIES) % SS_ENTRIES, on = k == SS_HIGH;
        unsigned ink = on && !ss.focus ? 0xffffff : on ? primary : secondary;
        if (on && ss_popping()) continue; /* ss_pop_paint draws the slot */
        if (on && !ss.focus)
            fill_box(canvas, &(rect_t){ x - 2, y0 + 7, SS_STEP, SS_STRIP_H - 14 }, RGBA(accent), 5);
        unsigned c[2] = { ss_char(e), 0 };
        stock_canvas_set_fill_color(canvas, RGBA(ink));
        if (e == SS_SPACE) { /* an open box under the line, the keyboard's space */
            canvas_fill_rect(canvas, x + 2, cy + 4, 11, 2);
            canvas_fill_rect(canvas, x + 2, cy, 2, 4);
            canvas_fill_rect(canvas, x + 11, cy, 2, 4);
        } else if (e == SS_DELETE) { /* a left-pointing arrow */
            canvas_fill_rect(canvas, x + 2, cy - 1, 11, 2);
            for (int d = 1; d <= 4; ++d) {
                canvas_fill_rect(canvas, x + 2 + d, cy - 1 - d, 2, 1);
                canvas_fill_rect(canvas, x + 2 + d, cy + d, 2, 1);
            }
        } else {
            rect_t r = { x - 2, y0, SS_STEP, SS_STRIP_H };
            int px = SS_PX_LETTER;
            const char *face = on ? "inter-medium" : theme_face(c, 1, &px);
            final_text(canvas, face, c, &r, (unsigned)px, 0xff000000u | ink);
        }
    }
    if (ss_popping()) ss_pop_paint(canvas);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}

#define SS_TAP 8 /* a touch that moves less than this is a tap */
static int ss_dist(int d) { return d < 0 ? -d : d; }

/* The sheet's press, and the page's release wherever it lands (as 3EQ's sliders take theirs). */
static int ss_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    if (!ss.sheet) return 0;
    widget_to_local(ss.sheet, xy);
    if (I(event, EVENT_TYPE) == EVT_POINTER_DOWN) {
        ss.down_x = (short)xy[0], ss.down_y = (short)xy[1], ss.pressed = 1;
        return 0;
    }
    if (!ss.pressed) return 0;
    ss.pressed = 0;
    int dx = xy[0] - ss.down_x, dy = xy[1] - ss.down_y,
        tap = ss_dist(dx) < SS_TAP && ss_dist(dy) < SS_TAP;
    if (ss.down_y >= SS_STRIP_Y) {
        int k = (ss.down_x - (SS_BOX_R + 8)) / SS_STEP;
        ss.focus = 0;
        if (!tap)
            ss.letter = ((ss.letter - dx / SS_STEP) % SS_ENTRIES + SS_ENTRIES) % SS_ENTRIES;
        else if (ss.down_x >= SS_BOX_R + 8 && k < SS_SLOTS) {
            ss.letter = (ss.letter - SS_HIGH + k + SS_ENTRIES) % SS_ENTRIES;
            ss_type(ss.letter);
        }
    } else if (!tap) {
        int most = ss.nmatch > SS_ROWS ? ss.nmatch - SS_ROWS : 0;
        ss.top -= dy / SS_PITCH;
        ss.top = ss.top < 0 ? 0 : ss.top > most ? most : ss.top;
        if (ss.sel < ss.top) ss.sel = ss.top;
        if (ss.sel >= ss.top + SS_ROWS) ss.sel = ss.top + SS_ROWS - 1;
    } else if (ss.down_y >= 0 && ss.top + ss.down_y / SS_PITCH < ss.nmatch &&
               ss.down_y / SS_PITCH < SS_ROWS) {
        ss.focus = 1;
        ss.sel = ss.top + ss.down_y / SS_PITCH;
        ss_play();
    }
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

/* The Library's Search row and Local Songs' pull: the strip under a list.entry.ipod theme, stock's
 * search dialog under the others. */
int library_search_open(void *ctx, void *event) {
    if (!theme()->entry_ipod) return stock_search(ctx, event);
    if (ss.page || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup))) return 0;
    int n;
    ss.songs = staged_music(all_songs, (void *)1, &n);
    n = ss.songs ? (int)deque_size(ss.songs) : 0;
    ss.match = n ? calloc((unsigned)n + 2 * SS_GROUP, sizeof *ss.match) : (void *)0;
    ss.kind = ss.match ? calloc((unsigned)n + 2 * SS_GROUP, 1) : (void *)0;
    if (!ss.kind) free(ss.match), ss.match = (void *)0;
    void *title = text(ss.page, 0, 0, 375, 28); /* what the status row reads */
    widget_set_text_utf8(title, "Search");
    widget_set_name(title, PAGE_TITLE);
    widget_set_visible(title, 0, 0);
    ss.sheet = widget_factory_create_widget(widget_factory(), "view", ss.page, 0, 0, 375,
                                            widget_get_prop_int(ss.page, "h", client_h()));
    widget_on(ss.sheet, EVT_POINTER_DOWN, ss_touch, 0);
    widget_on(ss.page, EVT_POINTER_UP_BEFORE, ss_touch, 0);
    return 0;
}
