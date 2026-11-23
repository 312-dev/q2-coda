/* Search as an iPod classic's letter strip (list.entry.ipod, the OLED theme's;
 * docs/ipod.md#search): the Library's Search row opens a page with the query on its first line,
 * a pill low in the glass, the letter strip, and what matches in the list between them, filtered
 * as each character goes in: the songs, then the artists, then the albums. While the wheel is on
 * the results, tabs above them (All, Songs, Artists, Albums, an iOS segmented control) pick what
 * the list holds: All the three groups, SS_GROUP of each at most, the others one group in full.
 * The tab picked stays while the text changes.
 *
 * The strip (docs/oled-design.md#text-entry, the user's mock-up keyboard-inscreen/strip.html) is
 * one loop the wheel walks, its stops: the set switcher docked at the pill's left (abc, ABC, 123,
 * #+=: every printable ASCII character between them), the set's characters and a space, and the
 * return key docked at its right, a circle as tall as the strip. The run of characters starts at
 * its first and scrolls only when the highlight comes within a cell of an edge, its letters
 * fading out where more of the set lies past that edge. One turn of the wheel (ticks closer than
 * SS_GESTURE_MS) stops at either end, the switcher or the return key; a new turn goes on round to
 * the other.
 *
 * Keys on the strip: Centre types the character at the text's cursor, cycles the set on the
 * switcher (the highlight stays there), and submits on the return key; Play/Pause submits from
 * anywhere on it; Return shuts the strip (ss_shut: never a submit; an edge swipe does the same,
 * search_strip_close), the next Return leaves, and held SS_HOLD_MS goes back (its card,
 * search_strip_overlay); while it is shut the strip is not drawn and Search's results run to the
 * screen's foot (ss_foot). Previous and Next move the cursor along the text, and
 * Previous held erases the character before it, again and again, faster the longer it is held
 * (ss_erase_due). Submitting shuts the strip too; on Search the wheel goes to the results (the
 * tabs while the tab picked lists none, the query when nothing matches): the wheel moves their bar,
 * Centre opens a song, which plays with the songs after it as the queue, an artist (stock's
 * artist page) or an album (its tracks in Cover Flow). A turn up from the first result lands on
 * the tabs, where the wheel moves between them, each picked as it is reached
 * (docs/oled-design.md#tabs): a turn down past Albums, or Centre, goes back to the results, a
 * turn up past All to the query line, and Next picks the next tab and Previous the one before.
 * The query line then takes the selection bar and nothing else (SS_ON_FIELD): the strip stays shut,
 * the wheel goes on down to the tabs, and Centre or a tap opens the strip. Previous on the results
 * goes back to the strip, the text, cursor and tab kept; Return there or on the tabs leaves. On a
 * name's or a box's page the shut strip leaves the text in place, the wheel doing nothing, and
 * Play/Pause is still its OK. A held Return closes Search from any of them (search_strip_hold, from
 * Hold Return's compact_now_playing). Off the strip Play/Pause is the player's.
 *
 * The key pop (ss_pop_paint): when the wheel or a drag lands on a character, or a tap types one,
 * its key grows a bubble holding it large, and the key keeps it at the strip's size, dimmed; it
 * goes SS_POP_MS after the wheel stops. A spin faster than SS_POP_FAST_MS a step shows none until
 * it stops, then one. The bubble and its big characters are tools/art.py's images (pop_images),
 * drawn at whole pixels.
 *
 * Touch (ss_touch): a drag along the strip moves the highlight a cell per 19 px as the finger
 * goes, popping as the wheel does, and types nothing; a tap on a character types it, on the
 * switcher cycles the set and on the return key submits; a tap on a tab picks it, a tap on a
 * result opens or plays it, and a drag up or down the results scrolls them a row per 32 px. In the
 * text (ss_grab, the mock-up's caretAt), a tap puts the cursor at the nearest character boundary
 * and gives the wheel back to the strip (while the wheel is off the strip the tap counts at the
 * release, and a drag from the query is no tap); a hold or a drag shows a loupe over the cursor,
 * the text at 1.6 times (ss_loupe_paint), and the cursor follows the finger; held at an edge that
 * cuts the text, the finger nudges it a character at a time (ss_nudge_due). The release leaves the
 * cursor. A double tap selects a word and a triple tap the text (ss_sel), the accent behind it and
 * a handle at either end; a drag on from the double tap, or on a handle, moves an end (ss_reach).
 * Typing replaces a selection, Previous held deletes it, and Previous or Next collapses it.
 *
 * The page is one sheet painted here (search_strip_paint), not rows: the strip and the list share
 * the wheel, and only one of them shows a selection at a time.
 *
 * One strip for Search, the query over the results, for naming a playlist: New Playlist
 * (playlist_new_open) and Rename Playlist (playlist_rename_open, prefilled), and for a text box
 * (search_strip_box, search_strip_text). The text is typed where it lives (the user's
 * keyboard-inscreen mock-up, all-inputs.png; ss_place, ss_field): on Search's query line, in the
 * row or cell the wheel was on over the page under, or in a field under its label, with a blinking
 * cursor and no hint; submitting creates, saves or hands it on. Add Songs' Search row
 * (add_songs_search) is Search over songs alone. */
#include "navigation.h"

/* The sheet starts under the 58 px status row; on it, from the top: */
#define SS_PITCH 32 /* a result row */
#define SS_QUERY_Y                                                                                 \
    16 /* Search's query line: its centre, the text from SS_QUERY_X after the glass */
#define SS_QUERY_X 48
#define SS_GLASS_X                                                                                 \
    31 /* ... the magnifier's lens centred here, SS_GLASS_R px to its line's middle */
#define SS_GLASS_R 6
#define SS_RULE_Y 35  /* ... a 1 px rule under it, from x 24 to 351 */
#define SS_QUERY_H 40 /* ... and the results' top under it */
#define SS_STRIP_X 40 /* the strip, a pill from x 40 to 335 and y 254 to 288 on the page */
#define SS_STRIP_RX 335
#define SS_STRIP_Y 196
#define SS_STRIP_H 34
#define SS_STRIP_R 17
#define SS_TABS_X 16 /* the tabs: a 30 px segmented control in the first row, the rows' inset */
#define SS_TABS_Y (SS_QUERY_H + 1)
#define SS_TABS_H 30
#define SS_TABS_W (375 - 2 * SS_TABS_X)
#define SS_LIST_Y                                                                                  \
    (SS_QUERY_H + SS_PITCH) /* the results' top under the tabs, and the rows there                 \
                             */
#define SS_KEY_IN 5         /* the lit key's box, SS_KEY_IN in from the strip's top and bottom */
#define SS_CHIP_R 96        /* the set switcher, docked at the pill's left, to x 96 */
#define SS_RET_L (SS_STRIP_RX - SS_STRIP_H) /* the return key, a circle docked at its right */
#define SS_RUN_L 100 /* the run of characters between them, its first cell SS_PAD in */
#define SS_RUN_R (SS_RET_L - 4)
#define SS_PAD 6
#define SS_STEP 19                                            /* a character's cell */
#define SS_VISIBLE ((SS_RUN_R - SS_RUN_L - SS_PAD) / SS_STEP) /* cells in the run: 10 */
#define SS_FADE 22       /* the run's letters fade out over this at an edge with more past it */
#define SS_FADE_SLICE 2  /* ... a slice of this many columns at a time */
#define SS_RET_MARK 20   /* the return marks' images (tools/art.py RETURN_PX), centred on the key */
#define SS_TEXT_MAX 64   /* characters typed, at most (a WPA passphrase is 63) */
#define SS_TITLE_W 233   /* a result's title, at most; the artist takes the rest */
#define SS_ARTIST_MIN 40 /* an artist with less room than this is left out */
#define SS_RIGHT 347     /* the right column */
#ifndef CAT_MARK_W
#define CAT_MARK_W 20 /* the offline mark's ink (catalogue_mark_paint), centred on its x */
#endif
#define SS_MARK_X (SS_RIGHT - CAT_MARK_W / 2) /* a greyed row's mark: its ink ends at SS_RIGHT */
#define SS_MARK_GAP 12 /* a greyed row's text ends this far short of the mark's ink */
/* AWTK sizes a face by its line, 1.21 em for Inter: the design's 20, 17, 16 and 15 px */
#define SS_PX_ROW TYPE(20)
#define SS_PX_QUERY TYPE(20)
#define SS_PX_LETTER TYPE(17)
#define SS_PX_ARTIST TYPE(15)
/* Where the text lives (ss.form, ss_place; docs/oled-design.md#text-entry): */
enum {
    SS_FORM_QUERY, /* Search's query line, and a search box's */
    SS_FORM_ROW,   /* the row the wheel is on, the text its whole width (a playlist's name) */
    SS_FORM_CELL,  /* ... the text a cell after the row's label, at its right (Bluetooth's Name) */
    SS_FORM_FIELD  /* a field of its own under its label (a password, or a row not in sight) */
};
/* A row's or cell's box: the row's own place, x 16 to 359, in the raised grey with 8 px corners
 * and an accent line 2 px in; the label from x 24 in Text Secondary, the text from x 24 (a row)
 * or SS_CELL_GAP past the label and not left of SS_CELL_MIN (a cell), to the right column. The
 * page under shows down to SS_UNDER_GAP above the strip, or to the box with a note under it. */
#define SS_ROW_X 16
#define SS_ROW_W (375 - 2 * SS_ROW_X)
#define SS_ROW_R 8
#define SS_ROW_LINE 2
#define SS_ROW_TEXT 24
#define SS_CELL_GAP 36
#define SS_CELL_MIN 165
#define SS_UNDER_GAP 8
#define SS_NOTE_DY 24 /* a note's middle under its box (search_strip_note) */
/* A field: its label in 15 px Text Tertiary from x 40 on screen y 92, the field a raised pill
 * from x 40 to 335 and y 106 to 150 with 12 px corners, its text in Inter Medium 22 SS_FIELD_IN
 * in from either end, centred on y 128; a note on y 176 */
#define SS_FIELD_X 40
#define SS_FIELD_W 295
#define SS_FIELD_Y 48
#define SS_FIELD_H 44
#define SS_FIELD_R 12
#define SS_FIELD_IN 16
#define SS_LABEL_Y 34
#define SS_FIELD_NOTE_Y 118
#define SS_PX_NAME TYPE(22) /* Inter Medium 22, AWTK's size */
#define SS_PX_LABEL TYPE(15)
/* The text in its field (ss_field): the cursor kept SS_KEEP px in from either edge while the
 * text is wider than the field, which fades it out over SS_EDGE px at an edge that cuts it; the
 * cursor blinks, SS_BLINK_MS on and off, steady under Reduce Motion */
#define SS_KEEP 10
#define SS_EDGE 16
#define SS_BLINK_MS 530
/* A finger in the text (ss_grab, the mock-up's caretAt): a press within SS_GRAB_DX of the field's
 * ends and SS_GRAB_DY of its line. Held SS_LOUPE_HOLD_MS, or moved SS_TAP, it shows the loupe
 * (ss_loupe_paint, the mock-up's loupe()): a lens SS_LOUPE_W by SS_LOUPE_H with SS_LOUPE_R
 * corners, its foot the text's size and SS_LOUPE_GAP over its line, centred on the cursor and
 * kept SS_LOUPE_IN inside the screen, its text at 1.6 times (SS_ZOOM) kept SS_LOUPE_PAD in from its
 * sides, clear of the corners, its line SS_LOUPE_LINE; it grows in over SS_LOUPE_GROW_MS from half
 * its size, a frame each SS_LOUPE_STEP_MS, and shows whole at once under Reduce Motion. Held within
 * SS_KEEP of an edge that cuts the text, the finger nudges the cursor a character toward it every
 * SS_NUDGE_MS. */
#define SS_GRAB_DX 12
#define SS_GRAB_DY 22
#define SS_LOUPE_HOLD_MS 250
#define SS_LOUPE_W 104
#define SS_LOUPE_H 46
#define SS_LOUPE_R 14
#define SS_LOUPE_GAP 10
#define SS_LOUPE_IN 4
#define SS_LOUPE_PAD 4
#define SS_LOUPE_LINE RGB_TERTIARY_L /* the mock-up's #6E6E6E, in either half */
#define SS_LOUPE_GROW_MS 100
#define SS_LOUPE_STEP_MS 33
#define SS_ZOOM(v) (((v) * 8 + 2) / 5)
#define SS_NUDGE_MS 150
/* A selection (ss_sel, docs/oled-design.md#text-entry): a second tap within SS_MULTI_MS of a tap's
 * release and SS_MULTI_DX of it selects the word there, a third all of the text. The highlight is
 * the accent at SS_SEL_ALPHA behind the selected characters, the cursor's height; each end has a
 * 2 px line in the accent and an SS_DOT dot, over the start and under the end, which a press within
 * SS_HANDLE_DX of grabs. */
#define SS_MULTI_MS 350
#define SS_MULTI_DX 20
#define SS_SEL_ALPHA 0x66u /* 40% */
#define SS_DOT 8
#define SS_HANDLE_DX 12
enum {
    SS_DRAG_CARET,
    SS_DRAG_WORD,
    SS_DRAG_HANDLE
}; /* what a finger in the text moves (ss_reach) */
/* The set switcher: a #2C2C2E pill (the accent while the wheel is on it), its set's name in Inter
 * Medium 16 */
#define SS_CHIP_RGB RGB_RAISED_2
#define SS_PX_CHIP TYPE(16)  /* 16 */
#define SS_PX_SMALL TYPE(13) /* 13 */
#define SS_PEEK_MS 1000      /* a password's last character typed shows this long */
/* ss.secret: what the text is (ss_secret_box, search_strip_secret); a password shows as dots,
 * and a Wi-Fi network's joins when submitted */
enum { SS_SECRET_NONE, SS_SECRET_PASS, SS_SECRET_WIFI };
/* The key pop (tools/art.py POP_*): the bubble's image is the head's width, its foot the key's,
 * the key 18 px wide from the lit cell's left and SS_KEY_IN below the strip's top, as the lit
 * cell's accent box is; the big characters a strip of a head's box per character, in
 * tools/art.py POP_CHARS' order, the sets' (ss_pop_cell) */
#define SS_POP_W 30
#define SS_POP_H 66
#define SS_POP_HEAD 30
#define SS_POP_KEY_W 18
#define SS_POP_KEY_H 24
#define SS_POP_ECHO 0x99ffffffu /* the key's own character under the bubble: white at 60% */
#define SS_POP_MS 300           /* the pop goes this long after the wheel's last step */
#define SS_POP_FAST_MS                                                                             \
    125 /* steps closer than this, 8 a second, are a spin: no pop until it stops */
/* ticks of the wheel closer than this are one turn, which stops at the switcher or the return key
 */
#define SS_GESTURE_MS 250
/* songs, artists and albums listed at most on All (docs/ipod.md#search): the wheel moves its bar a
 * row a tick, 7 ticks a turn at the default Wheel Sensitivity, so the artists are under five turns
 * down; each artist and album is checked against those before it */
#define SS_GROUP 32
/* Return held (ss_hold_*): the card shows this long into the hold, the page closes at its end
 * (before stock's own long press, KEY_HOLD_MS), and the ring steps this often between */
#define SS_HOLD_SHOW_MS 200
#define SS_HOLD_MS 600
#define SS_HOLD_STEP_MS 33
/* Previous held (ss_erase_due): the first erase this long into the hold, the next SS_ERASE_GAP_MS
 * after it, and each after that 15% sooner than the one before, down to SS_ERASE_MIN_MS */
#define SS_ERASE_HOLD_MS 450
#define SS_ERASE_GAP_MS 500
#define SS_ERASE_MIN_MS 110
enum { SS_SONG, SS_ARTIST, SS_ALBUM, SS_NAME };                            /* a result's kind */
enum { SS_TAB_ALL, SS_TAB_SONGS, SS_TAB_ARTISTS, SS_TAB_ALBUMS, SS_TABS }; /* tab, kind + 1 */
enum { SS_ON_STRIP, SS_ON_LIST, SS_ON_TABS, SS_ON_FIELD };                 /* what has the wheel */

static struct {
    void *page, *sheet, *songs; /* the page, what it paints on, the library's songs A to Z */
    void **match;               /* the results: songs, artists, albums; a record of each */
    unsigned char *kind;        /* ... each one's SS_* */
    int nmatch, stop, off, focus, sel, top; /* the strip's stop (ss_stops), its run's first cell
                                             * shown; focus: SS_ON_* */
    int nsongs, tab, any; /* the songs first in match; the tab, SS_TAB_*; anything matches */
    int cur, by;          /* the cursor's byte in text (ss_step); ss_by_name's field */
    int rename;           /* Rename's playlist + 1 (playlist_rename_open), 0 for Search */
    int pick;             /* Add Songs' (add_songs_search): songs alone, Centre adds them */
    int create;           /* New Playlist's (playlist_new_open) */
    void *box; /* a stock text box's edit the strip types for (search_strip_box), or the page
                * itself for a text of the payload's own (search_strip_text) */
    const char *(*done)(const char *); /* ... the latter's, given the text when submitted */
    int max;                           /* ... the latter's characters, at most */
    const char *note;                  /* ... and who sees the text, under it (search_strip_note) */
    int secret, set;            /* the text is a password, SS_SECRET_*; the strip's set, SS_SET_* */
    unsigned typed_at, turn_at; /* the last character typed (shown a moment); the last tick */
    unsigned char halted;       /* this turn stopped on the switcher or the return key */
    short down_x, down_y, pressed, swiped; /* a touch: where it went down, cells it moved */
    char text[SS_TEXT_MAX + 1];
    unsigned pop_timer, pop_step_at; /* the pop's timer; the wheel's last step on the strip */
    unsigned char pop;               /* 1 shown, 2 a spin's, shown when it stops */
    unsigned hold_at, hold_timer;    /* Return's press (time_now_ms, 0 none), its timer */
    unsigned char held;              /* the hold's card shows */
    unsigned erase_timer, erase_gap; /* Previous held: its timer, the gap to the next erase */
    unsigned char prev_down, erased; /* Previous is down; its hold erased (the release is its) */
    unsigned peek_timer;             /* a password's last character typed: dots again when due */
    void *under;          /* the page typed for, painted once (under_snap), under a row's form */
    const char *label;    /* a cell's label, a field's (SS_FORM_CELL, SS_FORM_FIELD) */
    short form;           /* where the text lives, SS_FORM_* (ss_place) */
    short row_y, row_h;   /* ... a row's or cell's box on the sheet */
    short scroll;         /* the text's scroll in its field, px (ss_field) */
    short fx, fy, fw, fh; /* the field as last painted, on the sheet, for the blink to repaint */
    unsigned blink_at, blink_timer; /* the cursor shown from (its blink's start); the timer */
    /* The text as last painted (ss_field), for a finger in it and the loupe: each character
     * boundary's x on the sheet, the characters shown, the face and its size, the cursor's height
     */
    short edge[SS_TEXT_MAX + 1], nedge;
    const char *fface;
    int fpx, fch;
    unsigned char grab, loupe; /* a finger is in the text (ss_grab); the loupe shows */
    signed char nudge;         /* ... held at the edge the text is nudged toward, -1 or 1 */
    short grab_x;              /* ... where it is on the sheet */
    unsigned loupe_at, loupe_timer, nudge_timer; /* the loupe shown at; its timer; the nudge's */
    int ret; /* the return key's mark its caller asked for, STRIP_* (peq.h; ss_ret_kind) */
    /* The selection: from the byte mark - 1 (0: none) to the cursor, either way round (ss_sel); a
     * double tap's word, which a drag on from it keeps; the last tap's x and release, and how
     * many came in a row; what the finger in the text moves, SS_DRAG_* */
    short mark, word_a, word_b, tap_x;
    unsigned tap_at;
    unsigned char taps, drag;
    unsigned char
        fpress; /* a finger went down on the query while the strip had no wheel (ss_grab) */
    unsigned char shared; /* songs are Add Songs' list (add_songs_search), not the page's */
} ss __attribute__((section(".scratch")));

/* A list page's own search (search_bar.c, docs/oled-design.md#search-bar): the page's rows, its
 * object type in its context, filtered as the text is typed. SC_SONGS: a song table over
 * p_deque_showlist (Songs, an album's, a genre's), each found by its title, artist or album as
 * Search's songs are; SC_NAMES: a name table over it (Albums, Artists, Composers, Genres), by
 * field; SC_ROWS: a page's own rows (Playlists, Years, an artist's albums and songs, a playlist's
 * songs), by their first label's text. rec holds the page's rows as they were when it opened, in
 * the page's order, n of them; a result opens its row on the page (ss_scope_open): the page's
 * selection on it and a Centre press, so it does what the row does there. */
enum { SC_NONE, SC_SONGS, SC_NAMES, SC_ROWS };
static struct {
    int kind, field, n, target; /* target: the row to open once the page is on top again, + 1 */
    void **rec;
    void *page, *surface; /* the list page and its rows' surface (a table_client or scroll view) */
    unsigned timer;
    char label[48]; /* "Search Songs", the bar's words */
} sc __attribute__((section(".scratch")));
/* Search's results with no tabs: Add Songs' and a list page's own */
static int ss_alone(void) { return ss.pick || sc.kind; }
/* SC_ROWS' row w's words, its first hscroll_label's (or label's), into out */
static const char *sc_words(void *w, char *out, unsigned n) {
    out[0] = 0;
    for (unsigned i = 0, k = w ? widget_count_children(w) : 0; i < k && !out[0]; ++i) {
        void *c = widget_get_child(w, i);
        const char *t = widget_get_type(c);
        if (!tk_strcmp(t, "hscroll_label") || !tk_strcmp(t, "label"))
            widget_get_text_utf8(c, out, n);
        else if (widget_count_children(c))
            sc_words(c, out, n);
    }
    return out;
}
/* Return's hold closed the page: stock's long press, if it comes, is the hold's
 * (search_strip_hold), not the page under it */
static unsigned ss_hold_closed __attribute__((section(".scratch"))); /* when (time_now_ms | 1) */
#define SS_HOLD_LATE_MS 400 /* ... a long press this soon after is the same hold's */

/* The sets the switcher cycles (the user's mock-up): every printable ASCII character but the
 * space, which ends each; #+= the 32 symbols, those most used in passwords first. tools/art.py
 * POP_CHARS is these in this order. */
enum { SS_SET_LOWER, SS_SET_UPPER, SS_SET_DIGITS, SS_SET_SYMBOLS, SS_SETS };
static const char *const ss_sets[SS_SETS] = { "abcdefghijklmnopqrstuvwxyz",
                                              "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789",
                                              "!@#$%&*-_.?/+=:;,'\"()[]{}<>^~`\\|" };
static const char *const ss_set_names[SS_SETS] = { "abc", "ABC", "123", "#+=" };
/* The run: the set's characters, then the space. The stops: the switcher (0), the run's entries
 * (1 on), the return key (the last). */
static int ss_run(void) { return (int)strlen(ss_sets[ss.set]) + 1; }
static int ss_stops(void) { return ss_run() + 2; }
static int ss_on_ret(void) { return ss.stop == ss_stops() - 1; }
/* An end of the loop, the switcher or the return key: a turn stops on either (ss_turn). */
static int ss_on_end(void) { return !ss.stop || ss_on_ret(); }
static int ss_in_run(void) { return ss.stop > 0 && !ss_on_ret(); }
/* The run's entry e as text: the set's character, or the space. */
static unsigned ss_char(int e) {
    return e < ss_run() - 1 ? (unsigned char)ss_sets[ss.set][e] : ' ';
}
/* Entry e's cell in the pop's images (every set's characters in order), -1 the space's. */
static int ss_pop_cell(int e) {
    int at = 0;
    for (int i = 0; i < ss.set; ++i) at += (int)strlen(ss_sets[i]);
    return e < ss_run() - 1 ? at + e : -1;
}
/* The run kept about the highlight: shown from its first entry, scrolled only to keep the
 * highlight a cell in from either edge. ponytail: the run steps a whole cell at once, where the
 * mock-up eases it over 90 ms (as under Reduce Motion); an eased offset needs a repaint timer and
 * the run clipped to its cells. */
static void ss_follow(void) {
    if (!ss_in_run()) return;
    int r = ss.stop - 1, most = ss_run() - SS_VISIBLE;
    if (r < ss.off + 1) ss.off = r - 1 > 0 ? r - 1 : 0;
    if (r > ss.off + SS_VISIBLE - 2) ss.off = r - SS_VISIBLE + 2 < most ? r - SS_VISIBLE + 2 : most;
    if (ss.off < 0) ss.off = 0;
}

/* Whether the results from from on already hold field off's text of r, case aside. */
static int ss_listed(int from, int off, void *r) {
    for (int i = from; i < ss.nmatch; ++i)
        if (!strcasecmp(P(ss.match[i], off), P(r, off))) return 1;
    return 0;
}

static int ss_has(const char *s) { return s && *s && strcasestr(s, ss.text); }

/* The page names a playlist (New Playlist, Rename Playlist) or types for a text box: no results,
 * the text where it lives (ss.form). */
static int ss_named(void) { return ss.rename || ss.create || ss.box; }
/* Run entry e's cell's left on the sheet, as the run is scrolled now. */
static int ss_cell_x(int e) { return SS_RUN_L + SS_PAD + (e - ss.off) * SS_STEP; }

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
static void ss_scope_filter(void);
static void ss_filter(void) {
    int n = ss.songs ? (int)deque_size(ss.songs) : 0, all = ss.tab == SS_TAB_ALL;
    ss.nmatch = ss.sel = ss.top = ss.nsongs = ss.any = 0;
    if (!ss.text[0] || !ss.match) return;
    if (sc.kind) {
        ss_scope_filter();
        return;
    }
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

/* A list page's own search's results: its rows that hold the text, in the page's order, the page's
 * object type alone (no other groups): a song's title, artist or album, a name's own field, a
 * row's words. */
static void ss_scope_filter(void) {
    for (int i = 0; i < sc.n; ++i) {
        void *r = sc.rec[i];
        char name[512];
        int hit = sc.kind == SC_SONGS ? ss_has(track_name(name, sizeof name, r)) ||
                                            ss_has(P(r, REC_ARTIST)) || ss_has(P(r, REC_ALBUM))
                  : sc.kind == SC_NAMES ? I(r, REC_ID) >= 0 && ss_has(P(r, sc.field))
                                        : ss_has(sc_words(r, name, sizeof name));
        if (!hit) continue;
        ss.kind[ss.nmatch] = sc.kind == SC_SONGS ? SS_SONG : SS_NAME;
        ss.match[ss.nmatch++] = r;
    }
    ss.any = ss.nmatch != 0;
    ss.nsongs = sc.kind == SC_SONGS ? ss.nmatch : 0;
}

/* A password's last character typed has shown its second: the text is dots again. */
static int ss_peek_due(const void *unused) {
    ss.peek_timer = 0;
    if (ss.sheet) widget_invalidate_force(ss.sheet, (void *)0);
    return 0;
}

static void ss_cut_sel(int tidy);
/* Run entry e typed at the cursor: the set's character as it reads, or a space (on Search and a
 * name not first, nor beside another). What the strip types is ASCII, a byte each (ss_step). */
static void ss_type(int e) {
    char c = (char)ss_char(e);
    ss_cut_sel(c == ' '); /* typing replaces the selection; a space, never doubled */
    ss.mark = 0;
    int n = (int)strlen(ss.text), at = ss.cur;
    /* a box takes any space (a password may hold them); a name or a search no doubled one */
    if (n >= (ss.max ? ss.max : SS_TEXT_MAX) ||
        (c == ' ' && !ss.box && (!at || ss.text[at - 1] == ' ' || ss.text[at] == ' ')))
        return;
    unsigned now = (unsigned)time_now_ms();
    ss.typed_at = now ? now : 1; /* 0: nothing typed */
    if (ss.secret) rearm(&ss.peek_timer, ss_peek_due, SS_PEEK_MS);
    for (int i = n + 1; i > at; --i) ss.text[i] = ss.text[i - 1];
    ss.text[at] = c;
    ++ss.cur;
    if (!ss_named()) ss_filter();
}

/* ss.cur is a byte of the text, always a character's first: the strip types ASCII, but a text
 * typed elsewhere (a playlist's name, a Bluetooth name) is UTF-8, and its characters are stepped
 * over whole. A password shows a dot a byte (ss_shown), so it steps a byte at a time. */
static int ss_mid(int at) { return !ss.secret && (ss.text[at] & 0xc0) == 0x80; }
/* The byte a character on from at, dir 1 or -1; nothing past either end. */
static int ss_next(int at, int dir) {
    if (dir < 0 && at > 0) do
            --at;
        while (at > 0 && ss_mid(at));
    if (dir > 0 && ss.text[at]) do
            ++at;
        while (ss_mid(at));
    return at;
}
/* The cursor a character on, dir 1 or -1. */
static void ss_step(int dir) {
    ss.cur = ss_next(ss.cur, dir);
    ss.typed_at = 0; /* a password's peek is the character just typed alone */
}
/* The characters the text shows before byte at; the byte of shown character k. */
static int ss_chars(int at) {
    int k = 0;
    for (int i = 0; i < at && ss.text[i]; ++i) k += !ss_mid(i);
    return k;
}
static int ss_byte_of(int k) {
    int at = 0;
    for (; k > 0 && ss.text[at]; --k) do
            ++at;
        while (ss_mid(at));
    return at;
}

/* The selection's bytes, a to b; 0 while there is only the cursor. */
static int ss_sel(int *a, int *b) {
    int m = ss.mark - 1;
    if (!ss.mark || m == ss.cur) return 0;
    *a = m < ss.cur ? m : ss.cur, *b = m < ss.cur ? ss.cur : m;
    return 1;
}
/* The selection taken out, the cursor where it began; when tidy (Previous held) and not a box's
 * text, a space it leaves doubled, or first, goes with it, as no name or search types one. */
static void ss_cut_sel(int tidy) {
    int a, b;
    if (!ss_sel(&a, &b)) return;
    memmove(ss.text + a, ss.text + b, strlen(ss.text + b) + 1);
    if (tidy && !ss.box && ss.text[a] == ' ' && (!a || ss.text[a - 1] == ' '))
        memmove(ss.text + a, ss.text + a + 1, strlen(ss.text + a));
    ss.cur = a, ss.mark = 0;
    ss.typed_at = 0;
}
/* The word around byte at (spaces end it), or the run of spaces it is in: a double tap's. A
 * password's words don't show, so its word is all of it. */
static void ss_word(int at) {
    int a = at, b = at, sp;
    if (ss.secret) {
        ss.word_a = 0, ss.word_b = (short)strlen(ss.text);
        return;
    }
    if ((!ss.text[a] || ss.text[a] == ' ') && a > 0 && ss.text[a - 1] != ' ')
        --a; /* the word it ends */
    sp = ss.text[a] == ' ';
    while (a > 0 && (ss.text[a - 1] == ' ') == sp) --a;
    while (ss.text[b] && (ss.text[b] == ' ') == sp) ++b;
    ss.word_a = (short)a, ss.word_b = (short)b;
}
/* Previous or Next pressed: a selection collapsed to its start or end, else the cursor stepped. */
static void ss_move(int dir) {
    int a, b;
    if (ss_sel(&a, &b))
        ss.cur = dir < 0 ? a : b, ss.typed_at = 0;
    else
        ss_step(dir);
    ss.mark = 0;
}

/* The character before the cursor taken off (Previous held), or the selection; nothing at the
 * text's start. */
static void ss_erase(void) {
    int n = (int)strlen(ss.text), at = ss.cur, a, b;
    if (ss_sel(&a, &b)) {
        ss_cut_sel(1);
        if (!ss_named()) ss_filter();
        return;
    }
    ss.mark = 0;
    if (!at) return;
    ss_step(-1);
    memmove(ss.text + ss.cur, ss.text + at, (unsigned)(n - at + 1)); /* the peek went with it */
    if (!ss_named()) ss_filter();
}

/* A name submitted: the name, its edge spaces gone, saved (Rename) or made a playlist (New
 * Playlist, ringnav_playlist_create, which the list it goes back to opens on), and the page
 * closes; else a toast says why not and the name stays to edit. */
static void ss_save(void) {
    char name[SS_TEXT_MAX + 1];
    unsigned a = 0, b = strlen(ss.text);
    while (ss.text[a] == ' ') ++a;
    while (b > a && ss.text[b - 1] == ' ') --b;
    tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), ss.text + a);
    int r = !*name      ? 0
            : ss.create ? ringnav_playlist_create((void *)1, name)
                        : playlist_rename(ss.rename - 1, name);
    toast(!*name                ? "Name can't be empty"
          : r == -2             ? "A playlist has that name"
          : ss.create && r >= 0 ? "Playlist created"
          : ss.create           ? "Could not create playlist"
          : r                   ? "Renamed"
                                : "Could not rename playlist");
    if (*name && (ss.create ? r >= 0 : r > 0)) {
        g_delete_flag = 1; /* Playlists reads its rows again */
        navigator_back();
    }
}

/* A text box's end: its edit takes the text typed and the keyboard's OK is pressed
 * (input_method_dispatch_action, as stock's keyboard's Enter key does), so the box acts as it
 * always has: a Wi-Fi password joins, an EQ preset is renamed, a search searches. The strip is
 * on the keyboard's window (search_strip_box), so it goes with the keyboard; one the OK leaves
 * open is closed, as the keyboard's Done closes it. */
static void ss_box_done(void) {
    void *edit = ss.box, *im = input_method();
    char text[SS_TEXT_MAX + 1];
    tk_snprintf(text, sizeof text, "%s", ss.text);
    if (ss.done) { /* the payload's own text (search_strip_text): its page closes once taken */
        const char *why = ss.done(text);
        if (why)
            toast(why);
        else
            navigator_back();
        return;
    }
    if (!edit || !im || P(im, IM_WIDGET) != edit) return;
    widget_set_text_utf8(edit, text);
    input_method_dispatch_action(im);
    if (ss.box && P(im, IM_WIDGET) == edit) input_method_request(im, (void *)0);
}
/* Return held on a text box, or Return with its strip shut: the box's keyboard closes, the strip
 * with it, as the keyboard's Return did (navigation.c search_key: our own edits emptied first, so
 * their pages back out). Add Network's dialog is only the box, a modal one that stock leaves up for
 * a second Return to close: it is quit too, back to the page's rows with the text as it was, from
 * the next loop pass (quit inside the keyboard's own close, demo crashes). */
static unsigned ss_quit_timer __attribute__((section(".scratch")));
static void *ss_quit_win __attribute__((section(".scratch")));
static int ss_holds(void *root, void *w);
static int ss_quit_due(const void *unused) {
    ss_quit_timer = 0;
    if (ss_holds(window_manager(), ss_quit_win)) dialog_quit(ss_quit_win, 0);
    return 0;
}
static void ss_box_cancel(void) {
    void *edit = ss.box, *im = input_method();
    if (ss.done) { /* the payload's own text: its page closes, the text as it was */
        navigator_back();
        return;
    }
    if (!edit || !im || P(im, IM_WIDGET) != edit) return;
    if (!tk_strcmp(widget_get_prop_str(edit, "name", ""), EDIT_NAME))
        widget_set_text_utf8(edit, "");
    widget_set_focused(edit, 0);
    void *win = window_of(edit);
    int own = !tk_strcmp(widget_get_prop_str(win, "name", ""), "editwifi_dialog");
    input_method_request(im, (void *)0);
    if (own) ss_quit_win = win, rearm(&ss_quit_timer, ss_quit_due, 0);
}

/* The result at sel: a song plays, the songs found after it as the queue (a song list's class, as
 * Most Played's), which playing_page copies before it returns; an artist opens stock's artist page,
 * as Now Playing's Artist info does; an album its tracks in Cover Flow, as Era's albums do. Under
 * Add Songs, the song in or out of the playlist. */
extern int coverflow_goto_album(void *r);
static void ss_scope_go(void *r);
static void ss_play(void) {
    if (ss.sel >= ss.nmatch) return;
    void *r = ss.match[ss.sel], *dq;
    if (sc.kind) { /* a list page's own: its row, opened on the page (ss_scope_open) */
        ss_scope_go(r);
        return;
    }
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
    return (rect_t){ ss_cell_x(ss.stop - 1) - (SS_POP_W - SS_POP_KEY_W) / 2,
                     SS_STRIP_Y + SS_KEY_IN + SS_POP_KEY_H - SS_POP_H, SS_POP_W, SS_POP_H };
}
/* the pop shows over a character the wheel is on, not the space (it has no big form) */
static int ss_popping(void) {
    return ss.pop == 1 && !ss.focus && ss_in_run() && ss_pop_cell(ss.stop - 1) >= 0;
}
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
/* A step of the highlight, by the wheel or a drag (or a tap's character): the pop at once, or,
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
/* Run entry e in its cell from x, in argb: its character, or the space's open box under the
 * line (the keyboard's), as the strip's letters are drawn. */
static void ss_letter(void *canvas, int e, int x, unsigned argb) {
    int cy = SS_STRIP_Y + SS_STRIP_H / 2;
    unsigned c[2] = { ss_char(e), 0 };
    if (c[0] == ' ') {
        stock_canvas_set_fill_color(canvas, argb);
        canvas_fill_rect(canvas, x + 4, cy + 4, 11, 2);
        canvas_fill_rect(canvas, x + 4, cy, 2, 4);
        canvas_fill_rect(canvas, x + 13, cy, 2, 4);
        return;
    }
    rect_t r = { x, SS_STRIP_Y, SS_STEP, SS_STRIP_H };
    int px = SS_PX_LETTER;
    final_text(canvas, theme_face(c, 1, &px), c, &r, (unsigned)px, argb);
}
/* The bubble over the lit cell, the big character in its head, its echo on its key. */
static void ss_pop_paint(void *canvas) {
    rect_t b = ss_pop_box();
    int e = ss.stop - 1;
    ss_cell(canvas, "search_pop", 0, SS_POP_W, SS_POP_H, b.x, b.y);
    ss_cell(canvas, "search_pop_letters", ss_pop_cell(e), SS_POP_HEAD, SS_POP_HEAD, b.x, b.y);
    ss_letter(canvas, e, ss_cell_x(e), SS_POP_ECHO);
}

/* Whether the tabs show: while the wheel is off the strip, never under Add Songs (nor naming a
 * playlist, where it never leaves the strip). The rows showing, and the first one's top: under
 * the tabs while they show. */
static int ss_tabs(void) { return ss.focus && !ss_named() && !ss_alone(); }
static int ss_list_y(void) { return ss_tabs() ? SS_LIST_Y : SS_QUERY_H; }
/* The results' foot on the sheet: the strip's top while it is open, else the screen's foot (shut,
 * the strip is not drawn and the results fill the page; the sheet runs from the status row's foot
 * down, client_h). The rows that fit above it. */
static int ss_foot(void) { return ss.focus ? client_h() : SS_STRIP_Y; }
static int ss_rows(void) { return (ss_foot() - ss_list_y()) / SS_PITCH; }
/* The bar's row kept in view. */
static void ss_keep(void) {
    if (ss.sel < ss.top) ss.top = ss.sel;
    if (ss.sel >= ss.top + ss_rows()) ss.top = ss.sel - ss_rows() + 1;
}
static void ss_pick(int tab) {
    ss.tab = (tab + SS_TABS) % SS_TABS;
    ss_filter();
}

/* The strip closed, the page left as it is (docs/oled-design.md#text-entry, the user's 2026-11-18
 * ruling): Return or an edge swipe on the open strip, before either leaves the page. The wheel goes
 * to the text, highlighted on Search's query line, and nothing is typed or submitted; the text and
 * cursor stay, Centre or a tap in the text opens the strip again, and the next Return leaves. */
static void ss_ungrab(void);
static void ss_shut(void) {
    stop_timer(&ss.erase_timer);
    stop_timer(&ss.pop_timer);
    ss.prev_down = ss.erased = ss.pop = 0;
    ss.pressed = 0, ss.fpress = 0;
    if (ss.grab) ss_ungrab();
    ss.focus = SS_ON_FIELD;
}

/* The page left with nothing taken: a box's keyboard closed, else the page. */
static void ss_leave(void) {
    if (ss.box)
        ss_box_cancel();
    else
        navigator_back();
}

/* Return's hold: over, its card gone. */
static void ss_hold_end(void) {
    stop_timer(&ss.hold_timer);
    ss.hold_at = 0;
    if (ss.held && ss.page) widget_invalidate_force(ss.page, (void *)0);
    ss.held = 0;
}
/* The hold's card from SS_HOLD_SHOW_MS, its ring repainted as it fills, and at SS_HOLD_MS the page
 * closed. The release is then the hold's: the latch is armed, as stock's own long press arms it,
 * for stock's key-up filter to swallow it, and stock's long press, should Return still be down
 * at KEY_HOLD_MS, finds the page gone and does nothing (search_strip_hold). */
static int ss_hold_tick(const void *unused) {
    ss.hold_timer = 0;
    if (!ss.page || !ss.hold_at) return 0;
    unsigned now = (unsigned)time_now_ms();
    if (now - ss.hold_at >= SS_HOLD_MS) {
        ss_hold_end();
        *(volatile unsigned char *)RETURN_RELEASE_LATCH = 1;
        ss_hold_closed = now | 1;
        ss_leave();
        return 0;
    }
    ss.held = 1;
    widget_invalidate_force(ss.page, (void *)0); /* the card's ring, and the scrim's first frame */
    ss.hold_timer = timer_add(ss_hold_tick, (void *)0, SS_HOLD_STEP_MS);
    return 0;
}

/* The cursor's blink: shown from ss.blink_at for SS_BLINK_MS, hidden as long, and so on; shown
 * always under Reduce Motion. */
static int ss_blink_on(void) {
    return motion_reduced() || ((unsigned)time_now_ms() - ss.blink_at) / SS_BLINK_MS % 2 == 0;
}
/* The blink's turn: the field alone repainted, while the strip has the wheel (the cursor's). */
static int ss_blink_due(const void *unused) {
    ss.blink_timer = 0;
    if (!ss.sheet || ss.focus || motion_reduced()) return 0;
    widget_invalidate_force(ss.sheet, &(rect_t){ ss.fx, ss.fy, ss.fw, ss.fh });
    ss.blink_timer = timer_add(ss_blink_due, (void *)0, SS_BLINK_MS);
    return 0;
}
/* The sheet repainted after a key, the cursor shown from now (a blink starts over, as a text
 * field's cursor stays lit while it moves). */
static void ss_dirty(void) {
    if (!ss.sheet) return;
    unsigned now = (unsigned)time_now_ms();
    ss.blink_at = now;
    if (!ss.focus && !motion_reduced()) rearm(&ss.blink_timer, ss_blink_due, SS_BLINK_MS);
    widget_invalidate_force(ss.sheet, (void *)0);
}
/* Previous held: an erase, and the next one armed 15% sooner than this one was, to
 * SS_ERASE_MIN_MS. */
static int ss_erase_due(const void *unused) {
    ss.erase_timer = 0;
    if (!ss.page || !ss.prev_down || ss.focus) return 0;
    ss.erased = 1;
    ss_erase();
    ss_dirty();
    unsigned gap = ss.erase_gap ? ss.erase_gap * 85 / 100 : SS_ERASE_GAP_MS;
    ss.erase_gap = gap < SS_ERASE_MIN_MS ? SS_ERASE_MIN_MS : gap;
    ss.erase_timer = timer_add(ss_erase_due, (void *)0, ss.erase_gap);
    return 0;
}

/* ringnav_keydown: Return pressed on the page starts the hold's clock, and Previous pressed on the
 * strip the erase's (a repeat while either is held changes nothing). */
void search_strip_down(void *top, unsigned key) {
    if (!ss.page || top != ss.page) return;
    if (key == KEY_BACK_BTN && !ss.focus && !ss.prev_down) {
        ss.prev_down = 1;
        ss.erased = 0;
        ss.erase_gap = 0;
        rearm(&ss.erase_timer, ss_erase_due, SS_ERASE_HOLD_MS);
        return;
    }
    if (key != KEY_RETURN || ss.hold_at) return;
    unsigned now = (unsigned)time_now_ms();
    ss.hold_at = now ? now : 1;
    ss.held = 0;
    rearm(&ss.hold_timer, ss_hold_tick, SS_HOLD_SHOW_MS);
}

/* Submitted, by the return key's Centre, its tap or Play/Pause: a box's text to its box, a name
 * saved or created, Search's wheel to its results (to the tabs while the tab picked lists none). */
static void ss_submit(void) {
    if (ss.box)
        ss_box_done();
    else if (ss_named())
        ss_save();
    else /* the strip closes: on the results, else the tabs, else the query (nothing matches) */
        ss.focus = ss.nmatch               ? SS_ON_LIST
                   : ss.any && !ss_alone() ? SS_ON_TABS
                                           : SS_ON_FIELD,
        ss.sel = ss.top;
}

/* The highlight a stop on round the loop, dir 1 or -1. A tick of the turn that stopped on the
 * switcher or the return key (closer than SS_GESTURE_MS to the one before) goes nowhere; a new
 * turn goes on. */
static void ss_turn(int dir, int cont) {
    if (ss.halted && cont) return;
    ss.halted = 0;
    ss.stop = ((ss.stop + dir) % ss_stops() + ss_stops()) % ss_stops();
    ss.halted = (unsigned char)ss_on_end();
    ss_follow();
    if (ss_in_run()) ss_pop_step();
}

/* Centre on the strip: the switcher's next set, the highlight staying on it; the return key's
 * submit; the character typed. */
static void ss_centre(void) {
    if (!ss.stop) {
        ss.set = (ss.set + 1) % SS_SETS;
        ss.off = 0;
    } else if (ss_on_ret())
        ss_submit();
    else
        ss_type(ss.stop - 1);
}

/* ringnav: the page's keys before stock's (Play/Pause off the strip and a held Return's release
 * go on). */
int search_strip_key(void *top, unsigned key) {
    if (!ss.page || top != ss.page) return 0;
    if (key == KEY_RETURN && ss.hold_at) ss_hold_end(); /* let go before the page closed */
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    int wheel = key == KEY_NEXT   ? 1
                : key == KEY_PREV ? -1
                                  : 0,
        button = key == KEY_FWD_BTN    ? 1
                 : key == KEY_BACK_BTN ? -1
                                       : 0;
    if (button < 0) { /* Previous let go: its hold's erases, or a press's cursor step */
        int erased = ss.erased;
        stop_timer(&ss.erase_timer);
        ss.prev_down = ss.erased = 0;
        if (erased && !ss.focus) {
            *(volatile signed char *)KEY_TONE_LATCH = 0;
            return 1;
        }
    }
    if (ss.focus == SS_ON_LIST && (button < 0 || (wheel < 0 && !ss.sel))) {
        ss.focus = button ? SS_ON_STRIP : ss_alone() ? SS_ON_FIELD : SS_ON_TABS;
        wheel = button = 0, key = 0;
    }
    if (ss.focus && key == KEY_PLAY && ss_named())
        ss_submit(); /* a name's or a box's, its strip shut: still its OK */
    else if (ss.focus && key && !wheel && !button && key != KEY_CENTER && key != KEY_RETURN)
        return 0; /* Play/Pause, on the results */
    else if (ss.focus && key == KEY_RETURN)
        ss_leave(); /* the strip shut already: Return leaves, as on any page */
    else if (ss.focus == SS_ON_TABS) {
        int to = ss.tab + wheel; /* the wheel's tab, past either end off the tabs */
        if (to < 0)
            ss.focus = SS_ON_FIELD;
        else if (wheel && to < SS_TABS)
            ss_pick(to);
        else if ((wheel || key == KEY_CENTER) && ss.nmatch)
            ss.focus = SS_ON_LIST, ss.sel = ss.top;
        else if (button)
            ss_pick(ss.tab + button);
    } else if (ss.focus == SS_ON_FIELD) {
        /* the text, the strip shut: the wheel passes on down from Search's query, Centre types */
        if (key == KEY_CENTER)
            ss.focus = SS_ON_STRIP;
        else if (wheel > 0 && !ss_named())
            ss.focus = ss_alone() ? (ss.nmatch ? SS_ON_LIST : SS_ON_FIELD) : SS_ON_TABS,
            ss.sel = ss.top;
    } else if (ss.focus == SS_ON_LIST) {
        if (wheel && ss.sel + wheel >= 0 && ss.sel + wheel < ss.nmatch) ss.sel += wheel;
        if (key == KEY_CENTER) ss_play();
    } else if (!key) {
        /* the results' Previous, back on the strip */
    } else if (wheel) {
        unsigned now = (unsigned)time_now_ms();
        int cont = ss.turn_at && now - ss.turn_at < SS_GESTURE_MS;
        ss.turn_at = now ? now : 1;
        ss_turn(wheel, cont);
    } else if (key == KEY_CENTER)
        ss_centre();
    else if (key == KEY_PLAY)
        ss_submit();
    else if (key == KEY_RETURN) /* the strip shut, never a submit; the next Return leaves */
        ss_shut();
    else if (button)
        ss_move(button);
    else
        return 0;
    if (ss.page) { /* a submit may have closed it */
        ss_keep();
        ss_dirty();
    }
    /* stock's key-up clears the click latch, and ringnav skips it: the next press clicks */
    *(volatile signed char *)KEY_TONE_LATCH = 0;
    return 1;
}

/* Hold Return (compact_now_playing): on the page, the hold's own clock closes it (ss_hold_tick);
 * a hold whose press it never saw closes it at once, whatever has the wheel. Just after the hold's
 * clock closed it, the page under it takes nothing from the same hold. The latch stays armed for
 * stock's filter to swallow the release, as on the hold's other ways. */
int search_strip_hold(void *top) {
    unsigned closed = ss_hold_closed;
    ss_hold_closed = 0;
    if (closed && !ss.page && (unsigned)time_now_ms() - closed < SS_HOLD_LATE_MS) return 1;
    if (!ss.page || top != ss.page) return 0;
    if (!ss.hold_at) ss_leave();
    return 1;
}

/* Whether top is the strip's page, where Previous and Next are the text's cursor: no scan of the
 * song on their hold (seek.c), and no long press of stock's (hold_menu.c ringnav_keylong). */
int search_strip_owns(void *top) { return ss.page && top == ss.page; }
/* swipe_back.c: 2 when top is the strip's page with the strip open, 1 with it shut, else 0. */
int search_strip_open(void *top) { return !ss.page || top != ss.page ? 0 : ss.focus ? 1 : 2; }
/* swipe_back.c, an edge swipe claimed on top: the open strip shut in the page's place (ss_shut), 1;
 * 0 when top is not the strip's page or its strip is shut already, the swipe the page's. */
int search_strip_close(void *top) {
    if (search_strip_open(top) != 2) return 0;
    ss_shut();
    ss_dirty();
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

/* A line of s centred across the sheet on y, in face (0: the theme's for s) at px, in rgb; its
 * width. */
static int ss_centred(void *canvas, const char *face, const char *s, int y, int px, unsigned rgb) {
    unsigned wide[96];
    unsigned n = ss_wide(s, wide, sizeof wide / sizeof *wide);
    int fpx = px;
    if (!face) face = theme_face(wide, (int)n, &fpx);
    canvas_set_font(canvas, face, (unsigned)fpx);
    int w = (int)(canvas_measure_text(canvas, wide, n) + 0.5f);
    rect_t r = { (375 - w) / 2, y - SS_PITCH / 2, w + 2, SS_PITCH };
    final_text(canvas, face, wide, &r, (unsigned)fpx, 0xff000000u | rgb);
    return w;
}

/* Ink drawn by ss_fade: the ink at alpha (0 to 255) over arg's colour. */
typedef void ss_ink_t(void *canvas, const void *arg, unsigned alpha);

/* ink, which lies from x to x + w on the band y to y + h of the sheet, clipped to x0 to x1; its
 * columns within fade of x0 (when fl) or of x1 (when fr) drawn a slice of SS_FADE_SLICE columns at
 * a time under a clip, each slice in an alpha falling to 0 at that edge. The ink's own alpha fades
 * it, so nothing is painted over what lies under it (the strip's pill, a row's box, the page): the
 * run's letters at an edge with more of the set past it (ss_run_letter), and a field's text at an
 * edge that cuts it (ss_field). */
static void ss_fade(void *canvas, int x, int w, int y, int h, int x0, int x1, int fade, int fl,
                    int fr, ss_ink_t *ink, const void *arg) {
    int l = fl ? x0 + fade : x0, r = fr ? x1 - fade : x1, a0 = x > x0 ? x : x0,
        a1 = x + w < x1 ? x + w : x1, ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y), old[4],
        clip[4];
    int s0 = a0 > l ? a0 : l, s1 = a1 < r ? a1 : r;
    if (s0 < s1 && clip_within(canvas, old, clip, ox + s0, oy + y, s1 - s0, h)) {
        canvas_set_clip_rect(canvas, (rect_t *)clip);
        ink(canvas, arg, 255);
        canvas_set_clip_rect(canvas, (rect_t *)old);
    }
    for (int side = 0; side < 2; ++side) {
        int from = side ? (r > a0 ? r : a0) : a0, to = side ? a1 : (l < a1 ? l : a1);
        for (int c = from; c < to; c += SS_FADE_SLICE) {
            int sw = to - c < SS_FADE_SLICE ? to - c : SS_FADE_SLICE, mid = c + sw / 2,
                a = side ? 255 * (x1 - mid) / fade : 255 * (mid - x0) / fade;
            if (a <= 0) continue;
            if (!clip_within(canvas, old, clip, ox + c, oy + y, sw, h)) continue;
            canvas_set_clip_rect(canvas, (rect_t *)clip);
            ink(canvas, arg, (unsigned)(a > 255 ? 255 : a));
            canvas_set_clip_rect(canvas, (rect_t *)old);
        }
    }
}

/* A line of text for ss_fade: wide characters (0-ended) in face at px, left-aligned in box, in
 * rgb. */
struct ss_line {
    const unsigned *wide;
    const char *face;
    int px;
    rect_t box;
    unsigned rgb;
};
static void ss_line_ink(void *canvas, const void *arg, unsigned alpha) {
    const struct ss_line *l = arg;
    rect_t r = l->box;
    final_text(canvas, l->face, l->wide, &r, (unsigned)l->px, alpha << 24 | (l->rgb & 0xffffff));
}

/* The text where it lives (docs/oled-design.md#text-entry): s from x0 to x1, centred on y, in
 * face at px (face 0: the theme's for s), right-aligned to x1 when right and it fits, else from
 * x0; its cursor, 2 px by ch, after cur characters while the strip has the wheel and the blink
 * shows it. A text wider than the field scrolls by as little as keeps the cursor SS_KEEP px in
 * from either edge (ss.scroll, kept between paints as a text field keeps its own), and an edge
 * that cuts the text fades it out over SS_EDGE px inside the field (ss_fade). Where each
 * character boundary lies is kept (ss.edge), for a finger in the text and the loupe. */
static void ss_field(void *canvas, const char *s, int cur, int x0, int x1, int y, const char *face,
                     int px, int ch, int right, unsigned rgb) {
    unsigned wide[SS_TEXT_MAX * 3 + 1]; /* a password's dots are 3 bytes of UTF-8 each */
    unsigned n = ss_wide(s, wide, sizeof wide / sizeof *wide);
    int fpx = px;
    if (!face) face = theme_face(wide, (int)n, &fpx);
    canvas_set_font(canvas, face, (unsigned)fpx);
    if (n > SS_TEXT_MAX) n = SS_TEXT_MAX; /* the text is at most this many bytes */
    if (cur > (int)n) cur = (int)n;
    float run = 0;
    for (unsigned k = 0; k <= n; ++k) {
        ss.edge[k] = (short)(run + 0.5f);
        if (k < n) run += canvas_measure_text(canvas, wide + k, 1);
    }
    ss.nedge = (short)n, ss.fface = face, ss.fpx = fpx, ss.fch = ch;
    int room = x1 - x0, full = ss.edge[n] + 3, cx = cur > 0 ? ss.edge[cur] : 0, left;
    if (full <= room) {
        left = right ? x1 - full : x0;
        ss.scroll = 0;
    } else {
        int sc = ss.scroll;
        if (sc < cx - room + SS_KEEP) sc = cx - room + SS_KEEP;
        if (sc > cx - SS_KEEP) sc = cx - SS_KEEP;
        if (sc > full - room) sc = full - room;
        if (sc < 0) sc = 0;
        ss.scroll = (short)sc;
        left = x0 - sc;
    }
    for (unsigned k = 0; k <= n; ++k) ss.edge[k] = (short)(ss.edge[k] + left);
    ss.fx = (short)(x0 - 2), ss.fy = (short)(y - SS_PITCH / 2), ss.fw = (short)(room + 4),
    ss.fh = SS_PITCH;
    int a, b, sel = ss_sel(&a, &b);
    if (sel) { /* the highlight, under the text and inside the field */
        int ka = ss_chars(a), kb = ss_chars(b), h0 = ss.edge[ka > (int)n ? (int)n : ka],
            h1 = ss.edge[kb > (int)n ? (int)n : kb];
        if (h0 < x0) h0 = x0;
        if (h1 > x1) h1 = x1;
        stock_canvas_set_fill_color(canvas,
                                    (RGBA(accent_tone(2)) & 0xffffffu) | SS_SEL_ALPHA << 24);
        if (h0 < h1) canvas_fill_rect(canvas, h0, y - 13, h1 - h0, (unsigned)ch);
    }
    if (n) {
        struct ss_line l = { wide, face, fpx, { left, y - SS_PITCH / 2, full + 2, SS_PITCH }, rgb };
        ss_fade(canvas, left, full + 2, y - SS_PITCH / 2, SS_PITCH, x0, x1, SS_EDGE,
                left<x0, left + full> x1, ss_line_ink, &l);
    }
    if (sel) { /* a handle at either end in view: its line, a dot over the start, under the end */
        for (int end = 0; end < 2; ++end) {
            int k = ss_chars(end ? b : a), hx = ss.edge[k > (int)n ? (int)n : k];
            if (hx < x0 - 1 || hx > x1 + 1) continue;
            stock_canvas_set_fill_color(canvas, RGBA(accent_tone(2)));
            canvas_fill_rect(canvas, hx - 1, y - 13, 2, (unsigned)ch);
            fill_box(canvas,
                     &(rect_t){ hx - SS_DOT / 2, end ? y - 13 + ch - 2 : y - 13 - SS_DOT + 2,
                                SS_DOT, SS_DOT },
                     RGBA(accent_tone(2)), SS_DOT / 2);
        }
        return; /* no cursor while there is a selection */
    }
    if (ss.focus || !ss_blink_on()) return;
    stock_canvas_set_fill_color(canvas, RGBA(tok(TOK_PRIMARY)));
    canvas_fill_rect(canvas, left + (cx ? cx + 1 : 0), y - 13, 2, (unsigned)ch);
}

/* The text as it shows into out: a password's characters as dots, but the one just typed for a
 * moment (SS_PEEK_MS). */
static const char *ss_shown(char *out) {
    if (!ss.secret) return ss.text;
    char *o = out;
    int n = (int)strlen(ss.text),
        peek = ss.cur && ss.typed_at && (unsigned)time_now_ms() - ss.typed_at < SS_PEEK_MS;
    for (int i = 0; i < n; ++i)
        if (peek && i == ss.cur - 1)
            *o++ = ss.text[i];
        else /* U+2022, a bullet */
            *o++ = (char)0xe2, *o++ = (char)0x80, *o++ = (char)0xa2;
    *o = 0;
    return out;
}

/* A row's or a cell's form: the page under as it was (ss.under), down to the strip or, with a
 * note, to the row; over the row the wheel was on, its box, a cell's label, and the text. A field's
 * form: its label over a pill holding the text. Either's note under it. */
static void ss_form_paint(void *canvas, unsigned primary, unsigned secondary, unsigned tertiary,
                          unsigned accent) {
    static const char *const faces[] = FONT_NAMES;
    char dots[SS_TEXT_MAX * 3 + 1];
    const char *shown = ss_shown(dots);
    int note_y;
    if (ss.form == SS_FORM_FIELD) {
        if (ss.label)
            ss_text(canvas, 0, ss.label, SS_FIELD_X, SS_LABEL_Y - SS_PITCH / 2, SS_PITCH,
                    SS_FIELD_W, SS_PX_LABEL, tertiary);
        fill_box(canvas, &(rect_t){ SS_FIELD_X, SS_FIELD_Y, SS_FIELD_W, SS_FIELD_H },
                 RGBA(tok(TOK_RAISED)), SS_FIELD_R);
        ss_field(canvas, shown, ss_chars(ss.cur), SS_FIELD_X + SS_FIELD_IN,
                 SS_FIELD_X + SS_FIELD_W - SS_FIELD_IN, SS_FIELD_Y + SS_FIELD_H / 2,
                 faces[FONT_INTER_MEDIUM], SS_PX_NAME, 26, 0, primary);
        note_y = SS_FIELD_NOTE_Y;
    } else {
        int y = ss.row_y, h = ss.row_h, end = ss.note ? y + h : SS_STRIP_Y - SS_UNDER_GAP;
        if (ss.under && end > 0) {
            int top = feedback_below(ss.sheet) - I(ss.sheet, W_H);
            rect_t src = { 0, top, 375, end }, dst = { 0, 0, 375, end };
            canvas_draw_image(canvas, ss.under, &src, &dst);
        }
        fill_box(canvas, &(rect_t){ SS_ROW_X, y, SS_ROW_W, h }, RGBA(accent), SS_ROW_R);
        fill_box(canvas,
                 &(rect_t){ SS_ROW_X + SS_ROW_LINE, y + SS_ROW_LINE, SS_ROW_W - 2 * SS_ROW_LINE,
                            h - 2 * SS_ROW_LINE },
                 RGBA(tok(TOK_RAISED)), SS_ROW_R - SS_ROW_LINE);
        int x0 = SS_ROW_TEXT;
        if (ss.form == SS_FORM_CELL && ss.label) {
            int lw =
                ss_text(canvas, 0, ss.label, SS_ROW_TEXT, y, h, SS_ROW_W / 2, SS_PX_ROW, secondary);
            x0 = SS_ROW_TEXT + lw + SS_CELL_GAP;
            if (x0 < SS_CELL_MIN) x0 = SS_CELL_MIN;
        }
        ss_field(canvas, shown, ss_chars(ss.cur), x0, SS_RIGHT, y + h / 2, 0, SS_PX_ROW, 25,
                 ss.form == SS_FORM_CELL, primary);
        note_y = y + h + SS_NOTE_DY;
    }
    if (ss.note) ss_centred(canvas, 0, ss.note, note_y, SS_PX_SMALL, tertiary);
}

/* The loupe (the mock-up's loupe(), docs/oled-design.md#text-entry), over w after stock painted
 * it: the top window, and the status row it may reach over, each painting its part. Centred on the
 * cursor (kept SS_LOUPE_IN inside the screen), its foot the text's own size (AWTK's px back to the
 * design's) and SS_LOUPE_GAP over the text's line, at least SS_LOUPE_IN from the screen's top; an
 * SS_LOUPE_LINE outline round a lens in the field's own ground (the canvas under Search's query,
 * the raised grey in a row, cell or field); in it the text drawn again by the same renderer at
 * 1.6 times (SS_ZOOM: the face's size, the field's ends and fades), the cursor's boundary at its
 * middle under a steady 3 px cursor; a selection's highlight under the text too, and the cursor in
 * the accent, its moving end's handle. Grows in from half its size about its foot (ss_loupe_due).
 */
static int ss_fx0(void), ss_fx1(void), ss_fcy(void), ss_cut(int dir);
static void ss_loupe_paint(void *w, void *canvas) {
    void *bar = *(void *const *)system_bar, *lcd = P(canvas, CANVAS_LCD);
    if (!ss.loupe || !ss.sheet || !ss.fface || !lcd ||
        (w != bar && w != window_manager_get_top_window(window_manager())))
        return;
    char dots[SS_TEXT_MAX * 3 + 1];
    unsigned wide[SS_TEXT_MAX * 3 + 1],
        n = ss_wide(ss_shown(dots), wide, sizeof wide / sizeof *wide);
    if (n > (unsigned)ss.nedge) n = (unsigned)ss.nedge, wide[n] = 0;
    int k = ss_chars(ss.cur), at, cx, top, grown, lw, lh;
    if (k > (int)n) k = (int)n;
    at = ss.edge[k];
    cx = at < SS_LOUPE_IN + SS_LOUPE_W / 2         ? SS_LOUPE_IN + SS_LOUPE_W / 2
         : at > 375 - SS_LOUPE_IN - SS_LOUPE_W / 2 ? 375 - SS_LOUPE_IN - SS_LOUPE_W / 2
                                                   : at;
    top = feedback_below(ss.sheet) - I(ss.sheet, W_H) + ss_fcy() - (ss.fpx * 100 + 60) / 121 -
          SS_LOUPE_GAP - SS_LOUPE_H;
    if (top < SS_LOUPE_IN) top = SS_LOUPE_IN;
    top -= feedback_below(w) - I(w, W_H); /* on w */
    grown = motion_reduced() ? SS_LOUPE_GROW_MS : (int)((unsigned)time_now_ms() - ss.loupe_at);
    if (grown > SS_LOUPE_GROW_MS) grown = SS_LOUPE_GROW_MS;
    lw = SS_LOUPE_W * (SS_LOUPE_GROW_MS + grown) / (2 * SS_LOUPE_GROW_MS);
    lh = SS_LOUPE_H * (SS_LOUPE_GROW_MS + grown) / (2 * SS_LOUPE_GROW_MS);
    rect_t box = { cx - lw / 2, top + SS_LOUPE_H - lh, lw, lh },
           lens = { box.x + 1, box.y + 1, lw - 2, lh - 2 };
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    fill_box(canvas, &box, RGBA(SS_LOUPE_LINE), SS_LOUPE_R);
    fill_box(canvas, &lens, RGBA(tok(ss.form == SS_FORM_QUERY ? TOK_CANVAS : TOK_RAISED)),
             SS_LOUPE_R - 1);
    int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y), old[4], clip[4],
        mid = top + SS_LOUPE_H / 2, zpx = SS_ZOOM(ss.fpx), zh = SS_ZOOM(ss.fch);
    if (clip_within(canvas, old, clip, ox + lens.x, oy + lens.y, lens.w, lens.h)) {
        canvas_set_clip_rect(canvas, (rect_t *)clip);
        canvas_set_font(canvas, ss.fface, (unsigned)zpx);
        int zat = k ? (int)(canvas_measure_text(canvas, wide, (unsigned)k) + 0.5f) : 0,
            zfull = (int)(canvas_measure_text(canvas, wide, n) + 0.5f) + 3, lx = cx - zat,
            f0 = cx - SS_ZOOM(at - ss_fx0()), f1 = cx + SS_ZOOM(ss_fx1() - at),
            c0 = box.x + SS_LOUPE_PAD, c1 = box.x + lw - SS_LOUPE_PAD, fl = ss_cut(-1) && f0 >= c0,
            fr = ss_cut(1) && f1 <= c1;
        if (f0 > c0) c0 = f0;
        if (f1 < c1) c1 = f1;
        int a, b, sel = ss_sel(&a, &b);
        if (sel) { /* the selection's highlight, as the field's, at 1.6 times */
            int ka = ss_chars(a), kb = ss_chars(b);
            ka = ka > (int)n ? (int)n : ka, kb = kb > (int)n ? (int)n : kb;
            int h0 = lx + (int)(canvas_measure_text(canvas, wide, (unsigned)ka) + 0.5f),
                h1 = lx + (int)(canvas_measure_text(canvas, wide, (unsigned)kb) + 0.5f);
            h0 = h0 < c0 ? c0 : h0, h1 = h1 > c1 ? c1 : h1;
            stock_canvas_set_fill_color(canvas,
                                        (RGBA(accent_tone(2)) & 0xffffffu) | SS_SEL_ALPHA << 24);
            if (h0 < h1) canvas_fill_rect(canvas, h0, mid - zh / 2, h1 - h0, (unsigned)zh);
        }
        if (n && c0 < c1) {
            struct ss_line l = { wide,
                                 ss.fface,
                                 zpx,
                                 { lx, mid - SS_ZOOM(SS_PITCH) / 2, zfull + 2, SS_ZOOM(SS_PITCH) },
                                 tok(TOK_PRIMARY) };
            ss_fade(canvas, lx, zfull + 2, lens.y, lens.h, c0, c1, SS_ZOOM(SS_EDGE), fl, fr,
                    ss_line_ink, &l);
        }
        /* the cursor, or the moving end's handle line in the accent */
        stock_canvas_set_fill_color(canvas, RGBA(sel ? accent_tone(2) : tok(TOK_PRIMARY)));
        canvas_fill_rect(canvas, cx - 1, mid - zh / 2, 3, (unsigned)zh);
        canvas_set_clip_rect(canvas, (rect_t *)old);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* ringnav_paint, after stock painted w: Return held, the screen dimmed by black at 55%
 * (overlay_scrim, the status row too, as Buttons Lock's card dims it), and over the page, centred
 * on screen y 160, a #1C1C1E card with 22 px corners, sized to its line with 24 px sides and 20 px
 * above and below: a 44 px ring (a 4 px #3A3A3C track, the accent filling it clockwise from the top
 * over the hold) round Phosphor's arrow-u-up-left (bold, 20 px, white), and 14 px under it "Keep
 * holding to go back" in Inter Medium 16, white, in either half. */
#define SS_HOLD_CARD_MID 160      /* on the screen */
#define SS_HOLD_SCRIM 0x8c000000u /* black at 55%, Buttons Lock's (keylock.c KEYLOCK_DIM) */
#define SS_HOLD_RING 22           /* the ring's radius to its line's middle */
static void ss_bar_over(void *w, void *canvas);
void search_strip_overlay(void *w, void *canvas) {
    static const char *const faces[] = FONT_NAMES;
    static const char line[] = "Keep holding to go back";
    int dy;
    ss_bar_over(w, canvas);
    ss_loupe_paint(w, canvas);
    if (!ss.held || !ss.page || !overlay_scrim(w, canvas, SS_HOLD_SCRIM, &dy)) return;
    unsigned wide[32], n = ss_wide(line, wide, sizeof wide / sizeof *wide),
                       fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int px = 19; /* Inter Medium 16 */
    canvas_set_font(canvas, faces[FONT_INTER_MEDIUM], (unsigned)px);
    int tw = (int)(canvas_measure_text(canvas, wide, n) + 0.5f), cw = tw + 48, ch = 122,
        y0 = SS_HOLD_CARD_MID - dy - ch / 2;
    fill_box(canvas, &(rect_t){ (375 - cw) / 2, y0, cw, ch }, RGBA(RGB_RAISED), 22);
    float cx = 187.5f, cy = (float)(y0 + 44),
          frac = (float)((unsigned)time_now_ms() - ss.hold_at - SS_HOLD_SHOW_MS) /
                 (SS_HOLD_MS - SS_HOLD_SHOW_MS);
    frac = frac < 0 ? 0 : frac > 1 ? 1 : frac;
    void *vg = canvas_get_vgcanvas(canvas);
    if (vg) {
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        vgcanvas_set_line_width(vg, 4);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, cy, SS_HOLD_RING, 0, 6.2831853f, 0);
        vgcanvas_set_stroke_color(vg, RGBA(RGB_OFF));
        vgcanvas_stroke(vg);
        if (frac > 0) {
            vgcanvas_begin_path(vg);
            vgcanvas_arc(vg, cx, cy, SS_HOLD_RING, -1.5707963f, -1.5707963f + frac * 6.2831853f, 0);
            vgcanvas_set_stroke_color(vg, RGBA(accent_tone(2)));
            vgcanvas_stroke(vg);
        }
        vgcanvas_restore(vg);
    }
    paint_art(w, canvas, theme_art("holdback"), (int)cx - 10, (int)cy - 10);
    ss_centred(canvas, faces[FONT_INTER_MEDIUM], line, y0 + 20 + 48 + 14 + 10, px, 0xffffff);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Search's query line, and a search box's: the magnifier's lens and handle in Text Secondary, the
 * query in 20 px from SS_QUERY_X with its cursor while the strip has the wheel (ss_field; or the
 * placeholder, ss.label, in Text Tertiary while it is empty), and a 1 px rule in the raised grey
 * under it. */
/* The magnifier, its ring's centre at x, y in canvas's own coordinates, in rgb. */
static void ss_glass(void *canvas, int x0, int y0, unsigned rgb) {
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    float cx = (float)(I(canvas, CANVAS_X) + x0), cy = (float)(I(canvas, CANVAS_Y) + y0);
    vgcanvas_save(vg);
    vgcanvas_set_line_width(vg, 1.8f);
    vgcanvas_set_stroke_color(vg, RGBA(rgb));
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, cx, cy, SS_GLASS_R, 0, 6.2831853f, 0);
    vgcanvas_stroke(vg);
    vgcanvas_begin_path(vg);
    vgcanvas_move_to(vg, cx + 4.5f, cy + 4.5f);
    vgcanvas_line_to(vg, cx + 9, cy + 9);
    vgcanvas_stroke(vg);
    vgcanvas_restore(vg);
}

/* The search bar (docs/oled-design.md#search-bar, approved as mock_search_filter v3 on
 * 2026-11-19): a Raised 2 pill from x 16 to 359, SEARCH_PILL_H high round cy, the magnifier and,
 * with words (the empty text's placeholder), them after it: in Text Secondary while lit, when a 2
 * px accent outline goes round the pill and its inside stays as it was, else in Text Tertiary. A
 * list page's bar (search_bar.c) and its search's query line, the same bar. */
void search_pill_paint(void *canvas, int cy, int lit, const char *words) {
    rect_t r = { SEARCH_PILL_X, cy - SEARCH_PILL_H / 2, 375 - 2 * SEARCH_PILL_X, SEARCH_PILL_H };
    unsigned ink = tok(lit ? TOK_SECONDARY : TOK_TERTIARY);
    if (lit)
        fill_box(canvas, &(rect_t){ r.x - 2, r.y - 2, r.w + 4, r.h + 4 }, RGBA(accent_tone(2)),
                 SEARCH_PILL_R + 2);
    fill_box(canvas, &r, RGBA(tok(TOK_RAISED_2)), SEARCH_PILL_R);
    ss_glass(canvas, SS_GLASS_X, cy - 2, ink);
    if (words)
        ss_text(canvas, 0, words, SS_QUERY_X, cy - SS_PITCH / 2, SS_PITCH,
                SS_RIGHT + 4 - SS_QUERY_X, SS_PX_QUERY, ink);
}

static void ss_query_paint(void *canvas, unsigned primary, unsigned secondary, unsigned tertiary,
                           unsigned accent) {
    if (sc.kind) { /* a list page's own: its bar, lit while the strip or the wheel is on the text */
        int lit = ss.focus == SS_ON_STRIP || ss.focus == SS_ON_FIELD;
        search_pill_paint(canvas, SS_QUERY_Y, lit, ss.text[0] ? (const char *)0 : ss.label);
        ss_field(canvas, ss.text, ss_chars(ss.cur), SS_QUERY_X, SS_RIGHT + 4, SS_QUERY_Y, 0,
                 SS_PX_QUERY, 25, 0, primary);
        return;
    }
    if (ss.focus == SS_ON_FIELD) { /* the wheel is on the line: the selection bar, white on it */
        fill_box(canvas, &(rect_t){ 16, SS_QUERY_Y - SS_PITCH / 2, 375 - 32, SS_PITCH },
                 RGBA(accent), 8);
        primary = secondary = tertiary = 0xffffff;
    }
    ss_glass(canvas, SS_GLASS_X, SS_QUERY_Y - 2, secondary);
    if (!ss.text[0]) /* the placeholder, the cursor before it */
        ss_text(canvas, 0, ss.label ? ss.label : "Search", SS_QUERY_X, SS_QUERY_Y - SS_PITCH / 2,
                SS_PITCH, SS_RIGHT + 4 - SS_QUERY_X, SS_PX_QUERY, tertiary);
    ss_field(canvas, ss.text, ss_chars(ss.cur), SS_QUERY_X, SS_RIGHT + 4, SS_QUERY_Y, 0,
             SS_PX_QUERY, 25, 0, primary);
    stock_canvas_set_fill_color(canvas, RGBA(tok(TOK_RAISED)));
    canvas_fill_rect(canvas, 24, SS_RULE_Y, 375 - 48, 1);
}

/* Run entry e at x in rgb, faded out toward a run edge over SS_FADE where more of the set lies
 * past it (fade_l, fade_r; ss_fade). */
struct ss_cell {
    int e, x;
    unsigned rgb;
};
static void ss_cell_ink(void *canvas, const void *arg, unsigned alpha) {
    const struct ss_cell *c = arg;
    ss_letter(canvas, c->e, c->x, alpha << 24 | c->rgb);
}
static void ss_run_letter(void *canvas, int e, int x, unsigned rgb, int fade_l, int fade_r) {
    struct ss_cell c = { e, x, rgb };
    ss_fade(canvas, x, SS_STEP, SS_STRIP_Y, SS_STRIP_H, SS_RUN_L, SS_RUN_R, SS_FADE, fade_l, fade_r,
            ss_cell_ink, &c);
}

/* The return key's mark (docs/oled-design.md#text-entry), by what submitting does: the kind its
 * caller asked for (a search's magnifier, a name's or a value's check, the arrow), else a magnifier
 * on the query line (a search box no page names, as TIDAL's) and the arrow anywhere else. */
static int ss_ret_kind(void) {
    return ss.ret ? ss.ret : ss.form == SS_FORM_QUERY ? STRIP_SEARCH : STRIP_GO;
}

/* The strip (docs/oled-design.md#text-entry): a pill in the raised grey; the set switcher docked
 * at its left, its set's name on a #2C2C2E pill, the accent's while the wheel is on it; the
 * return key docked at its right, an accent circle with its mark (ss_ret_kind) in white, turned to
 * a white circle (in either half) with the mark in the accent while the wheel is on it; between
 * them the run, a cell of each character in Text Primary from the first shown, the lit one on an
 * accent box (or under its pop). The highlights show while the strip has the wheel. */
static void ss_strip_paint(void *canvas, unsigned primary, unsigned accent, int light) {
    int y0 = SS_STRIP_Y, on = !ss.focus;
    fill_box(canvas, &(rect_t){ SS_STRIP_X, y0, SS_STRIP_RX - SS_STRIP_X, SS_STRIP_H },
             RGBA(tok(TOK_RAISED)), SS_STRIP_R);
    int lit = on && ss_in_run() ? ss.stop - 1 : -1, pop = ss_popping(), run = ss_run();
    int fade_l = ss.off > 0, fade_r = ss.off + SS_VISIBLE < run;
    for (int e = ss.off; e < run && e < ss.off + SS_VISIBLE; ++e) {
        int x = ss_cell_x(e);
        if (e == lit && pop) continue; /* ss_pop_paint draws the cell */
        if (e == lit)
            fill_box(canvas,
                     &(rect_t){ x, y0 + SS_KEY_IN, SS_POP_KEY_W, SS_STRIP_H - 2 * SS_KEY_IN },
                     RGBA(accent), 5);
        ss_run_letter(canvas, e, x, e == lit ? 0xffffff : primary, fade_l, fade_r);
    }
    static const char *const faces[] = FONT_NAMES;
    unsigned w[8], n = ss_wide(ss_set_names[ss.set], w, sizeof w / sizeof *w);
    rect_t chip = { SS_STRIP_X, y0, SS_CHIP_R - SS_STRIP_X, SS_STRIP_H };
    fill_box(canvas, &chip, RGBA(on && !ss.stop ? accent : flip_if(light, SS_CHIP_RGB)),
             SS_STRIP_R);
    final_text(canvas, faces[FONT_INTER_MEDIUM], w, &chip, SS_PX_CHIP,
               0xff000000u | (on && !ss.stop ? 0xffffff : primary));
    (void)n;
    int ret = on && ss_on_ret();
    fill_box(canvas, &(rect_t){ SS_RET_L, y0, SS_STRIP_H, SS_STRIP_H },
             RGBA(ret ? 0xffffff : accent), SS_STRIP_R);
    static const char *const marks[][2] = {
        [STRIP_GO] = { "search_return_go_w", "search_return_go_a" },
        [STRIP_SEARCH] = { "search_return_search_w", "search_return_search_a" },
        [STRIP_SAVE] = { "search_return_save_w", "search_return_save_a" },
    };
    paint_art(ss.sheet, canvas, theme_art(marks[ss_ret_kind()][ret]),
              SS_RET_L + (SS_STRIP_H - SS_RET_MARK) / 2, y0 + (SS_STRIP_H - SS_RET_MARK) / 2);
    if (pop) ss_pop_paint(canvas);
}

/* ringnav_paint: the query, the results and the strip, on the sheet; or a name over the strip.
 * Colours are the theme's tokens (docs/oled-design.md#color), drawn as stated in each half. */
void search_strip_paint(void *w, void *canvas) {
    if (!ss.page || w != ss.sheet || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int light = ipod_light();
    unsigned primary = tok(TOK_PRIMARY), secondary = tok(TOK_SECONDARY),
             tertiary = tok(TOK_TERTIARY), accent = accent_tone(2);
    if (ss.form == SS_FORM_QUERY)
        ss_query_paint(canvas, primary, secondary, tertiary, accent);
    else
        ss_form_paint(canvas, primary, secondary, tertiary, accent);
    if (ss_tabs()) {
        static const char *const tabs[] = { "All", "Songs", "Artists", "Albums" };
        segments_paint(canvas, tabs, SS_TABS, ss.tab, ss.focus == SS_ON_TABS, SS_TABS_X, SS_TABS_Y,
                       SS_TABS_W, SS_TABS_H);
    }
    for (int i = 0; i < ss_rows() && ss.top + i < ss.nmatch && !ss_named(); ++i) {
        void *r = ss.match[ss.top + i];
        int y = ss_list_y() + i * SS_PITCH, on = ss.focus == SS_ON_LIST && ss.top + i == ss.sel;
        char name[512];
        if (on) fill_box(canvas, &(rect_t){ 16, y, 375 - 32, SS_PITCH }, RGBA(accent), 8);
        int kind = ss.kind[ss.top + i];
        const char *title =
            kind == SS_ARTIST  ? P(r, REC_ARTIST)
            : kind == SS_ALBUM ? P(r, REC_ALBUM)
            : kind == SS_NAME
                ? (sc.kind == SC_NAMES ? P(r, sc.field) : sc_words(r, name, sizeof name))
                : track_name(name, sizeof name, r);
        /* a row that can't play now (docs/server-library.md#grayed-rows): a server song offline,
         * an artist or album none of whose songs can play; its text CAT_GREY, on the bar the
         * bar's white at 70%, and the offline mark at its right, the text ending clear of it */
        int grey = kind == SS_NAME     ? 0
                   : kind == SS_ARTIST ? catalogue_artist_unavailable(r)
                   : kind == SS_ALBUM  ? catalogue_album_unavailable(r)
                                       : catalogue_unavailable(r),
            right = grey ? SS_MARK_X - CAT_MARK_W / 2 - SS_MARK_GAP : SS_RIGHT;
        /* the playing song's: its title in the accent and its bars on the right column
         * (song_rows.c, docs/oled-design.md#song-rows) */
        int playing = kind == SS_SONG && !grey && song_playing(r);
        unsigned ink = on ? (grey ? 0xb3ffffffu : 0xffffffffu)
                          : 0xff000000u | (grey ? 0x666666 : primary),
                 note = grey || on ? ink : 0xff000000u | tertiary;
        if (playing) {
            playing_bars(canvas, right + 1, y + SS_PITCH / 2, on);
            right -= PLAY_BARS_W + SONG_COL_GAP;
            if (!on) ink = 0xff000000u | (scheme()->tones[0] & 0xffffff);
        }
        int tw = ss_text(canvas, 0, title, 24, y, SS_PITCH,
                         SS_TITLE_W < right - 24 ? SS_TITLE_W : right - 24, SS_PX_ROW, ink);
        /* beside it, what the row is, as an iPod's result icons say: "Artist", "Album" and its
         * artist, or a song's artist */
        char what[300];
        const char *artist = kind == SS_NAME ? (const char *)0 : P(r, REC_ARTIST);
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
    if (ss.text[0] && !ss.nmatch && !ss_named()) { /* the empty state, between the tabs and strip */
        int y = I(ss.page, W_Y) + I(ss.sheet, W_Y), free = y + ss_list_y(); /* screen ys */
        static const empty_t none = { "magnifying-glass", "No Matches", "Try fewer letters", 0, 0, 0 };
        empty_t e = none;
        if (empty_compact(free)) e.glyph = 0; /* under the tabs, the compact form */
        empty_paint(ss.sheet, canvas, &e, empty_top(free, y + ss_foot(), 0) - y);
    }
    if (!ss.focus) ss_strip_paint(canvas, primary, accent, light); /* shut, it is not drawn */
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}

#define SS_TAP 8 /* a touch that moves less than this is a tap */
static int ss_dist(int d) { return d < 0 ? -d : d; }

/* The highlight moved n stops by a drag, as the wheel's turn: a drag is one turn, stopping at
 * either end. */
static void ss_slide(int n) {
    for (int i = 0; i < (n < 0 ? -n : n); ++i) ss_turn(n < 0 ? -1 : 1, ss.swiped || i);
}

/* The text's field as last painted (ss_field): its ends on the sheet, and its line. */
static int ss_fx0(void) { return ss.fx + 2; }
static int ss_fx1(void) { return ss.fx + ss.fw - 2; }
static int ss_fcy(void) { return ss.fy + SS_PITCH / 2; }

/* The loupe repainted where it was and where it is: the page, and the status row it may reach
 * over. */
static void ss_loupe_dirty(void) {
    void *bar = *(void *const *)system_bar;
    widget_invalidate_force(ss.page, (void *)0);
    if (bar) widget_invalidate_force(bar, (void *)0);
}
/* The loupe's turn: shown at the hold's end, then grown in a frame at a time. */
static int ss_loupe_due(const void *unused);
static void ss_loupe_show(void) {
    unsigned now = (unsigned)time_now_ms();
    ss.loupe = 1;
    ss.loupe_at = now;
    if (motion_reduced())
        stop_timer(&ss.loupe_timer);
    else
        rearm(&ss.loupe_timer, ss_loupe_due, SS_LOUPE_STEP_MS);
    ss_loupe_dirty();
}
static int ss_loupe_due(const void *unused) {
    ss.loupe_timer = 0;
    if (!ss.page || !ss.grab) return 0;
    if (!ss.loupe) {
        ss_loupe_show();
        return 0;
    }
    ss_loupe_dirty();
    if ((unsigned)time_now_ms() - ss.loupe_at < SS_LOUPE_GROW_MS)
        ss.loupe_timer = timer_add(ss_loupe_due, (void *)0, SS_LOUPE_STEP_MS);
    return 0;
}

/* The boundary nearest x on the sheet, as a shown character's index (ss.edge). */
static int ss_nearest(int x) {
    int best = 0;
    for (int k = 1; k <= ss.nedge; ++k)
        if (ss_dist(ss.edge[k] - x) < ss_dist(ss.edge[best] - x)) best = k;
    return best;
}
/* Whether the field cuts the text at its left (dir -1) or right (1), as last painted. */
static int ss_cut(int dir) {
    return dir < 0 ? ss.edge[0] < ss_fx0() : ss.edge[ss.nedge] + 3 > ss_fx1();
}
/* The finger at byte at: the cursor there; a selection's moving end there (a handle's, kept off
 * the other end), or, on from a double tap, the word and on to there. */
static void ss_reach(int at) {
    if (ss.drag == SS_DRAG_WORD) {
        int out = at < ss.word_a || at > ss.word_b;
        ss.mark = (short)((at < ss.word_a ? ss.word_b : ss.word_a) + 1);
        ss.cur = out ? at : ss.word_b;
    } else if (ss.drag != SS_DRAG_HANDLE || at != ss.mark - 1)
        ss.cur = at;
}
/* The finger's nudge: the cursor a character on toward the edge it is held at, while the text is
 * cut there; once that end of the text is in view, the cursor follows the finger again. */
static void ss_aim(void);
static int ss_nudge_due(const void *unused) {
    ss.nudge_timer = 0;
    if (!ss.page || !ss.grab || !ss.nudge) return 0;
    if (!ss_cut(ss.nudge)) {
        ss.nudge = 0;
        ss_aim();
        return 0;
    }
    ss_reach(ss_next(ss.cur, ss.nudge));
    ss.typed_at = 0;
    ss_dirty();
    ss_loupe_dirty();
    ss.nudge_timer = timer_add(ss_nudge_due, (void *)0, SS_NUDGE_MS);
    return 0;
}
/* The cursor (or what the finger holds, ss_reach) where the finger is: at the boundary nearest it,
 * or, with the loupe up and the finger held SS_KEEP from an edge that cuts the text, there and
 * then a character on toward the edge, at once and every SS_NUDGE_MS after (ss_nudge_due). */
static void ss_aim(void) {
    int x = ss.grab_x, dir = !ss.loupe                              ? 0
                             : x < ss_fx0() + SS_KEEP && ss_cut(-1) ? -1
                             : x > ss_fx1() - SS_KEEP && ss_cut(1)  ? 1
                                                                    : 0;
    if (dir && dir == ss.nudge) return; /* nudging already */
    ss.nudge = (signed char)dir;
    ss.typed_at = 0;
    ss_reach(ss_next(ss_byte_of(ss_nearest(x)), dir));
    if (dir)
        rearm(&ss.nudge_timer, ss_nudge_due, SS_NUDGE_MS);
    else
        stop_timer(&ss.nudge_timer);
    ss_dirty();
    if (ss.loupe) ss_loupe_dirty();
}
/* The finger let go, or the page gone: the loupe and the nudge stop, the cursor where it is. */
static void ss_ungrab(void) {
    stop_timer(&ss.loupe_timer);
    stop_timer(&ss.nudge_timer);
    if (ss.loupe && ss.page) ss_loupe_dirty();
    ss.grab = ss.loupe = 0, ss.nudge = 0;
}
/* A finger in the text: a press within the field puts the cursor at the boundary nearest it and
 * gives the wheel back to the strip; held or moved, the loupe shows and the cursor follows it;
 * the release leaves the cursor where it is. A second press soon after a tap and near it selects
 * the word there, a third the text; a press on a selection's handle takes that end. 1 when the
 * event was the text's. */
static int ss_grab(int type, int x, int y) {
    unsigned now = (unsigned)time_now_ms();
    if (type == EVT_POINTER_DOWN) {
        if (!ss.fw || x < ss_fx0() - SS_GRAB_DX || x >= ss_fx1() + SS_GRAB_DX ||
            ss_dist(y - ss_fcy()) >= SS_GRAB_DY)
            return 0;
        int again = ss.taps && now - ss.tap_at < SS_MULTI_MS && ss_dist(x - ss.tap_x) < SS_MULTI_DX,
            a, b, end = -1;
        ss.down_x = ss.grab_x = (short)x, ss.down_y = (short)y;
        if (ss.focus !=
            SS_ON_STRIP) { /* the wheel is elsewhere: a tap is the click, a drag a scroll */
            ss.fpress = 1;
            return 1;
        }
        ss.grab = 1, ss.loupe = 0, ss.nudge = 0;
        ss.taps = (unsigned char)(again && ss.taps < 3 ? ss.taps + 1 : 1);
        ss.drag = SS_DRAG_CARET;
        rearm(&ss.loupe_timer, ss_loupe_due, SS_LOUPE_HOLD_MS);
        if (ss.taps == 1 && ss_sel(&a, &b)) { /* on a handle: the nearer end's */
            int da = ss_dist(ss.edge[ss_chars(a)] - x), db = ss_dist(ss.edge[ss_chars(b)] - x);
            end = db <= da && db < SS_HANDLE_DX ? b : da < SS_HANDLE_DX ? a : -1;
        }
        if (end >= 0) {
            if (end == ss.mark - 1) ss.mark = (short)(ss.cur + 1); /* the anchor's: the two swap */
            ss.cur = end, ss.taps = 0, ss.drag = SS_DRAG_HANDLE;
            ss_dirty();
        } else if (ss.taps == 1) {
            ss.mark = 0;
            ss_aim();
        } else { /* the word there, or all of it */
            ss_word(ss_byte_of(ss_nearest(x)));
            if (ss.taps == 3) ss.word_a = 0, ss.word_b = (short)strlen(ss.text);
            ss.drag = SS_DRAG_WORD;
            ss_reach(ss.word_b);
            ss.typed_at = 0;
            ss_dirty();
        }
        return 1;
    }
    if (ss.fpress) {
        if (type == EVT_POINTER_MOVE_BEFORE) {
            if (ss_dist(x - ss.down_x) >= SS_TAP || ss_dist(y - ss.down_y) >= SS_TAP) ss.fpress = 0;
            return 1;
        }
        ss.fpress =
            0; /* released where it went down: a tap, the strip has the wheel and the cursor */
        ss.focus = SS_ON_STRIP;
        ss_grab(EVT_POINTER_DOWN, ss.down_x, ss.down_y);
    }
    if (!ss.grab) return 0;
    if (type != EVT_POINTER_MOVE_BEFORE) { /* a tap's release starts the next tap's clock */
        if (ss.loupe || ss.drag == SS_DRAG_HANDLE)
            ss.taps = 0;
        else
            ss.tap_at = now, ss.tap_x = ss.down_x;
        ss_ungrab();
        ss_dirty();
        return 1;
    }
    ss.grab_x = (short)x;
    if (!ss.loupe && (ss_dist(x - ss.down_x) >= SS_TAP || ss_dist(y - ss.down_y) >= SS_TAP))
        ss_loupe_show();
    ss_aim();
    return 1;
}

/* The sheet's press, and the page's moves and release wherever they land (as 3EQ's sliders take
 * theirs). A press in the text is the text's (ss_grab). A drag on the strip moves the highlight
 * as it goes, to the left the later characters. */
static int ss_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) }, type = I(event, EVENT_TYPE);
    if (!ss.sheet) return 0;
    widget_to_local(ss.sheet, xy);
    if (ss_grab(type, xy[0], xy[1])) return 0;
    if (type == EVT_POINTER_DOWN) {
        ss.down_x = (short)xy[0], ss.down_y = (short)xy[1], ss.pressed = 1, ss.swiped = 0;
        return 0;
    }
    if (!ss.pressed) return 0;
    int dx = xy[0] - ss.down_x, dy = xy[1] - ss.down_y,
        tap = ss_dist(dx) < SS_TAP && ss_dist(dy) < SS_TAP,
        strip = ss.down_y >= SS_STRIP_Y && !ss.focus; /* shut, the strip is not there */
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
        int x = ss.down_x,
            e = x < SS_RUN_L + SS_PAD ? -1 : (x - SS_RUN_L - SS_PAD) / SS_STEP + ss.off;
        ss.focus = SS_ON_STRIP;
        if (!tap || x < SS_STRIP_X || x >= SS_STRIP_RX) {
            /* a drag, or off the pill */
        } else if (x < SS_CHIP_R) { /* the switcher: the next set */
            ss.stop = 0, ss.halted = 1;
            ss_centre();
        } else if (x >= SS_RET_L) { /* the return key: submitted */
            ss.stop = ss_stops() - 1, ss.halted = 1;
            ss_submit();
            if (!ss.page) return 0; /* it closed the page */
        } else if (e >= 0 && e < ss_run() && e < ss.off + SS_VISIBLE && x < SS_RUN_R) {
            ss.stop = e + 1, ss.halted = 0;
            ss_follow();
            ss_pop_step();
            ss_type(e);
        }
    } else if (ss_named()) {
        /* a name or a box's text: nothing to scroll or pick */
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
    ss_dirty();
    return 0;
}

/* Whether w is in the tree under root (w only compared, never read: it may be gone). */
static int ss_holds(void *root, void *w) {
    if (!root || !w) return 0;
    if (root == w) return 1;
    for (unsigned i = 0, n = widget_count_children(root); i < n; ++i)
        if (ss_holds(widget_get_child(root, i), w)) return 1;
    return 0;
}

static void ss_band_back(void);
static void ss_load_end(void);
static int ss_closed(void *ctx, void *event) {
    stop_timer(&ss.pop_timer);
    stop_timer(&ss.hold_timer);
    stop_timer(&ss.erase_timer);
    stop_timer(&ss.peek_timer);
    stop_timer(&ss.blink_timer);
    stop_timer(&ss.loupe_timer);
    stop_timer(&ss.nudge_timer);
    ss_band_back();
    if (sc.kind && !sc.target) { /* a list page's own search, nothing to open after it */
        free(sc.rec);
        memset(&sc, 0, sizeof sc);
    }
    if (ss.under) bitmap_destroy(ss.under);
    ss_load_end();
    if (ss.songs && !ss.shared) deque_destroy(ss.songs);
    free(ss.match);
    free(ss.kind);
    memset(&ss, 0, sizeof ss);
    return 0;
}

/* The page and its sheet, titled caption on the status row. */
static void ss_sheet(const char *caption) {
    void *title = text(ss.page, 0, bar_foot(ss.page), 375, 28); /* what the status row reads */
    widget_set_text_utf8(title, caption);
    widget_set_name(title, PAGE_TITLE);
    widget_set_visible(title, 0, 0);
    ss.sheet = widget_factory_create_widget(widget_factory(), "view", ss.page, 0, bar_foot(ss.page),
                                            375, page_room(ss.page));
    widget_on(ss.sheet, EVT_POINTER_DOWN, ss_touch, 0);
    widget_on(ss.page, EVT_POINTER_MOVE_BEFORE, ss_touch, 0);
    widget_on(ss.page, EVT_POINTER_UP_BEFORE, ss_touch, 0);
}

/* Where the text lives (docs/oled-design.md#text-entry), as the strip opens over under, the page
 * it types for: Search's query line (SS_FORM_QUERY); a row or a cell (SS_FORM_ROW, SS_FORM_CELL)
 * in the place of the row the wheel's selection is on in under (navigation's st.sel_*), under
 * painted once under the strip (under_snap; the box's own edit hidden for it); a field of its own
 * (SS_FORM_FIELD) when that row is not in under or not clear of the strip. The cursor's blink
 * starts. */
static void ss_place(void *under, int form, void *edit) {
    ss.form = (short)form;
    ss_dirty();
    if (form != SS_FORM_ROW && form != SS_FORM_CELL) return;
    void *w = st.sel_w;
    int y = -1;
    if (ss_holds(under, w))
        y = feedback_below(w) - I(w, W_H) + st.sel_y - st.sel_h / 2 -
            (feedback_below(ss.sheet) - I(ss.sheet, W_H));
    if (y < 0 || st.sel_h <= 0 || y + st.sel_h > SS_STRIP_Y - SS_UNDER_GAP) {
        ss.form = SS_FORM_FIELD;
        return;
    }
    ss.row_y = (short)y, ss.row_h = (short)st.sel_h;
    int shown = edit && widget_get_visible(edit);
    if (shown) widget_set_visible(edit, 0, 0);
    ss.under = under_snap(under);
    if (shown) widget_set_visible(edit, 1, 0);
}

/* The Library's Search row and Local Songs' pull: the strip under a list.entry.ipod theme, stock's
 * search dialog under the others. */
/* The page's rows into sc.rec: a table's records (p_deque_showlist, its rows' own), else the
 * surface's rows, each a widget. The kind, as search_strip_scoped says it. */
static int sc_take(void *surface) {
    void *list = P(p_deque_showlist, 0), *win = window_of(surface);
    const char *page = win ? widget_get_prop_str(win, "name", "") : "";
    int table = !tk_strcmp(widget_get_type(surface), "table_client"), cls = I(g_class_type, 0);
    int n = table ? I(surface, TABLE_ROWS) : (int)widget_count_children(surface);
    if (table && (!list || (int)deque_size(list) != n)) return SC_NONE;
    if (n <= 0 || !(sc.rec = calloc((unsigned)n, sizeof *sc.rec))) return SC_NONE;
    for (int i = 0; i < n; ++i)
        sc.rec[i] = table ? deque_at(list, (unsigned)i) : widget_get_child(surface, (unsigned)i);
    sc.n = n;
    if (!table) return SC_ROWS;
    static const unsigned char field[] = { REC_ALBUM, REC_ARTIST, 0x20,
                                           REC_STYLE }; /* az_index.c's */
    if ((!tk_strcmp(page, "localclass_page") || !tk_strcmp(page, "album_page")) &&
        cls >= CLASS_ALBUMS && cls <= 0xf006) {
        sc.field = field[cls - CLASS_ALBUMS];
        return SC_NAMES;
    }
    return SC_SONGS;
}

/* A list page's search bar's Centre (search_bar.c): the strip over the page's own rows, titled as
 * the page, the bar's words "Search <title>"; Return shuts the strip, the wheel then on the text
 * and the results kept, and Return again goes back to the page, its bar lit; the go key goes to the
 * first result, or with none to the text. 1 when it opened. */
int search_strip_scoped(void *page, void *surface, const char *title) {
    if (ss.page || !page || !surface) return 0;
    memset(&sc, 0, sizeof sc);
    int kind = sc_take(surface);
    if (!kind || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup))) {
        free(sc.rec);
        memset(&sc, 0, sizeof sc);
        return 0;
    }
    sc.kind = kind, sc.page = page, sc.surface = surface;
    ss.match = calloc((unsigned)sc.n + 1, sizeof *ss.match);
    ss.kind = ss.match ? calloc((unsigned)sc.n + 1, 1) : (void *)0;
    if (!ss.kind) free(ss.match), ss.match = (void *)0;
    ss.set = SS_SET_LOWER, ss.stop = 1;
    ss.ret = STRIP_SEARCH;
    tk_snprintf(sc.label, sizeof sc.label, "Search %s", title && *title ? title : "");
    ss.label = sc.label;
    ss_sheet(title && *title ? title : "Search");
    ss_place(0, SS_FORM_QUERY, 0);
    return 1;
}

/* Once the page is on top again, settled: its row target - 1 selected and Centre pressed, as
 * through the window manager's input, so it does there what it does. */
extern int ringnav_input(void *wm, void *e);
static int ss_scope_due(const void *unused) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!sc.target || !sc.page) return sc.timer = 0, 0;
    if (top != sc.page || window_manager_is_animating(wm)) return 8; /* RET_REPEAT */
    int id = sc.target - 1;
    sc.target = 0, sc.timer = 0;
    search_bar_off(); /* the selection on the row, the bar out of sight */
    if (!tk_strcmp(widget_get_type(sc.surface), "table_client"))
        ringnav_jump(sc.surface, id);
    else
        ringnav_select(sc.surface, id, sc.n);
    unsigned e[16] = { 0 };
    e[0] = EVT_KEY_DOWN;
    e[EVENT_KEY / 4] = KEY_CENTER;
    e[EVENT_TIME / 4] = (unsigned)time_now_ms();
    ringnav_input(wm, e);
    e[0] = EVT_KEY_UP;
    ringnav_input(wm, e);
    free(sc.rec);
    memset(&sc, 0, sizeof sc);
    return 0;
}
/* Centre on result r: the strip's page goes, and the page opens r's row (ss_scope_due). */
static void ss_scope_go(void *r) {
    int i = 0;
    while (i < sc.n && sc.rec[i] != r) ++i;
    if (i == sc.n) return;
    sc.target = i + 1;
    void *page = ss.page;
    sc.timer = timer_add(ss_scope_due, (void *)0, 16);
    window_close(page);
}

/* Search's songs, the whole library (staged_music of all_songs, the server's songs too, in stock's
 * title order), are loaded on a thread of their own as the page opens, so the strip is up and
 * takes letters at once; the results come with the load (ss_load_due), the text typed by then
 * filtered as it ends. A payload load on the UI thread meanwhile waits for it (search_yield, from
 * coverflow.c staged_into), and the page closing waits for it too, so nothing else meets its use
 * of stock's staging pointer and library_music. With no thread the load runs in place. */
static struct {
    unsigned long thread;
    volatile int running, done;
    void *songs; /* the load's, until ss_load_due takes it */
    unsigned timer;
} sl __attribute__((section(".scratch")));
static void *ss_load_worker(void *unused) {
    int n;
    sl.songs = staged_music(all_songs, (void *)LIBRARY_MUSIC, &n);
    __atomic_store_n(&sl.done, 1, __ATOMIC_RELEASE);
    return 0;
}
/* The songs loaded: the results' room, and the text's results. */
static void ss_take(void *songs) {
    int n = songs ? (int)deque_size(songs) : 0;
    ss.songs = songs;
    ss.match = n ? calloc((unsigned)n + 2 * SS_GROUP, sizeof *ss.match) : (void *)0;
    ss.kind = ss.match ? calloc((unsigned)n + 2 * SS_GROUP, 1) : (void *)0;
    if (!ss.kind) free(ss.match), ss.match = (void *)0;
}
static void ss_load_join(void) {
    if (!sl.running) return;
    pthread_join(sl.thread, 0);
    sl.running = 0;
}
static int ss_load_due(const void *unused) {
    if (!__atomic_load_n(&sl.done, __ATOMIC_ACQUIRE)) return 8; /* RET_REPEAT */
    sl.timer = 0;
    ss_load_join();
    ss_take(sl.songs); /* the page is open: its close stops this timer */
    sl.songs = 0, sl.done = 0;
    if (!ss_named()) ss_filter();
    if (ss.sheet) widget_invalidate_force(ss.sheet, (void *)0);
    return 0;
}
/* The page closing: a load still running waited for, its songs dropped. */
static void ss_load_end(void) {
    stop_timer(&sl.timer);
    ss_load_join();
    if (sl.songs) deque_destroy(sl.songs);
    memset(&sl, 0, sizeof sl);
}
/* staged_into on the UI thread: Search's load, if one runs, ends first. */
void search_yield(void) {
    if (sl.running && pthread_self() == ui_thread) ss_load_join();
}

int library_search_open(void *ctx, void *event) {
    if (!theme()->entry_ipod) return stock_search(ctx, event);
    if (ss.page || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup))) return 0;
    coverflow_yield(); /* a Cover Flow open's step gives way first: one load at a time */
    unsigned long t = 0;
    sl.done = 0, sl.songs = 0;
    sl.running = !pthread_create(&t, 0, ss_load_worker, 0) && t;
    if (sl.running)
        sl.thread = t, sl.timer = timer_add(ss_load_due, (void *)0, 16);
    else {
        int n;
        ss_take(staged_music(all_songs, (void *)LIBRARY_MUSIC, &n));
    }
    ss.set = SS_SET_UPPER, ss.stop = 1; /* on A (the user's mock-up) */
    ss.ret = STRIP_SEARCH;
    ss.label = "Search your music";
    ss_sheet("Search");
    ss_place(0, SS_FORM_QUERY, 0);
    return 0;
}

/* A playlist's Rename (its hold menu, docs/oled-design.md#playlists): the strip, the name typed in
 * the playlist's row, prefilled, the cursor at its end; submitting saves it (playlists.c
 * playlist_rename), and a held Return closes the page and leaves the name as it was. */
void playlist_rename_open(int id, const char *name) {
    if (ss.page || id < 0 || id >= 100 ||
        !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup)))
        return;
    ss.rename = id + 1, ss.stop = 1, ss.ret = STRIP_SAVE;
    tk_snprintf(ss.text, sizeof ss.text, "%s", name ? name : "");
    ss.cur = (int)strlen(ss.text);
    ss_sheet("Rename Playlist");
    ss_place(window_under(window_manager(), ss.page), SS_FORM_ROW, 0);
}

/* New Playlist (stock's add-playlist dialog's place, playlists.c): the same page, empty;
 * submitting makes the playlist (ringnav_playlist_create). */
void playlist_new_open(void) {
    if (ss.page || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup))) return;
    ss.create = 1, ss.stop = 1, ss.ret = STRIP_SAVE;
    ss_sheet("New Playlist");
    ss_place(window_under(window_manager(), ss.page), SS_FORM_ROW, 0);
}

/* Add Songs' Search row (add_songs.c): the strip over songs, Add Songs' own Songs list (the
 * library's songs, as Search loads them), which stays Add Songs' to free; each found by its title
 * or artist, with its mark; Centre on a result adds it or takes it out, and the results stay. */
void add_songs_search(void *songs) {
    if (!theme()->entry_ipod || ss.page ||
        !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup)))
        return;
    ss_take(songs);
    ss.shared = 1;
    ss.set = SS_SET_UPPER, ss.stop = 1;
    ss.ret = STRIP_SEARCH;
    ss.pick = 1;
    ss.tab = SS_TAB_SONGS;
    ss.label = "Search songs";
    ss_sheet("Search");
    ss_place(0, SS_FORM_QUERY, 0);
}

/* What a text box's edit takes, SS_SECRET_*. Stock's Wi-Fi password boxes say so by neither
 * input_type (its pages' "password" reads back as 0, text) nor anything else on the edit:
 * a network's own (kbwifipass_dialog) is the edit named edit_passwd, and Add Network's two rows
 * both type in editwifi_dialog, whose init keeps which in EDITWIFI_PASSWORD. A payload box marks
 * itself _secret (addon.c search_box, a card app's password field). */
static int ss_secret_box(void *edit) {
    if (!tk_strcmp(widget_get_prop_str(edit, "name", ""), "edit_passwd") ||
        (!tk_strcmp(widget_get_prop_str(window_of(edit), "name", ""), "editwifi_dialog") &&
         *(volatile int *)EDITWIFI_PASSWORD))
        return SS_SECRET_WIFI;
    return widget_get_prop_int(edit, "_secret", 0) ? SS_SECRET_PASS : SS_SECRET_NONE;
}

/* A network's password (kbwifipass_dialog) and Add Network's box (editwifi_dialog) are stock
 * dialogs of the whole screen, black, the box in a row at the status row's height with stock's
 * back arrow beside it; the window manager paints dialogs after the status row, so that band
 * stood over the bar and its title (the user's screenshot, 2026-11-19). While the strip types, its
 * window covers the dialog below the bar, so the dialog's children in the band hide and its
 * ground is cleared (as the confirmation card clears stock's, feedback.c): the status row shows
 * through. Under the bar then lies what the dialog's highlighter dims, Add Network's own dialog's
 * title row among it (seen on the emulator), so the bar lays a black ground of its own over it
 * (search_strip_bar_ground); the highlighter dims the bar too, so it is painted again over the
 * dialog (ss_bar_over). ss_band_back puts the dialog back, black as stock made it, if it is still
 * up (an OK that leaves it open). */
#define SS_BAND_MAX 4 /* the children hidden, at most */
static struct {
    void *win;
    void *hid[SS_BAND_MAX];
} band __attribute__((section(".scratch")));

static int ss_wifi_dialog(void *win) {
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    return !tk_strcmp(name, "kbwifipass_dialog") || !tk_strcmp(name, "editwifi_dialog");
}
static void ss_band_clear(void *win, int top) {
    int h = 0;
    memset(&band, 0, sizeof band);
    band.win = win;
    for (unsigned k = 0, n = widget_count_children(win); k < n && h < SS_BAND_MAX; ++k) {
        void *c = widget_get_child(win, k);
        if (!widget_get_visible(c) || I(c, W_Y) >= top) continue;
        widget_set_visible(c, 0, 0);
        band.hid[h++] = c;
    }
    widget_set_prop_int(win, "style:normal:bg_color", 0);
    widget_invalidate_force(window_manager(), (void *)0); /* the bar too */
}
static void ss_band_back(void) {
    if (band.win && ss_holds(window_manager(), band.win)) {
        widget_set_prop_int(band.win, "style:normal:bg_color", (int)0xff000000u);
        for (int k = 0; k < SS_BAND_MAX; ++k)
            if (band.hid[k]) widget_set_visible(band.hid[k], 1, 0);
    }
    memset(&band, 0, sizeof band);
}
/* ringnav_paint_bg, the status row's background (w the system bar): black under its glyphs while
 * a Wi-Fi dialog's band is cleared, over the highlighter's picture. */
void search_strip_bar_ground(void *w, void *canvas) {
    void *lcd =
        band.win && w && w == *(void *const *)system_bar ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, ui_ink(RGBA(0)));
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
}
/* ringnav_paint, after w's children (search_strip_overlay): the status row painted again over a
 * Wi-Fi box's cleared dialog, at full strength. The dialog's highlighter lays its dim over all
 * that lies under the dialog, the bar too, so the bar under it read at half strength. */
static void ss_bar_over(void *w, void *canvas) {
    void *bar = *(void *const *)system_bar;
    if (band.win && w == band.win && bar && P(canvas, CANVAS_LCD)) widget_paint(bar, canvas);
}

/* A box's label, kept for as long as the strip is up (the edit's own may go first). */
static char ss_box_label[48] __attribute__((section(".scratch")));

/* A stock text box's keyboard (keyboard.c kb_host_open, the input method's bare kb for edit): the
 * strip types for it on that window, moved over the screen under the status row, so nothing takes
 * the edit's focus (a page of its own would: a window to the background blurs its focus, which ends
 * a search box); titled as the page under it is, the box's text prefilled, the cursor at its end, a
 * password's characters as dots. The sets start on letters, or on digits for a box holding a
 * number. Where the text shows (ss_place): a Wi-Fi password or network name in a field under
 * "Password" or "Network Name"; a box whose page names its row (the edit's _strip_label: an EQ
 * preset's Name, a band's Frequency, a card app's field) in that row as a cell; any other box, a
 * search's, on the query line, its tips the placeholder. The edit's STRIP_RETURN picks the return
 * key's mark (ss_ret_kind). 1 when it opened. */
int search_strip_box(void *kb, void *edit, const char *title) {
    if (ss.page || !kb || !edit) return 0;
    int top = geom_val(V_BAR);
    ss.page = kb;
    widget_move_resize(kb, 0, top, 375, 320 - top);
    widget_set_prop_int(kb, "h", 320 - top);
    widget_on(kb, EVT_DESTROY, ss_closed, 0);
    ss.box = edit;
    ss.ret = widget_get_prop_int(edit, STRIP_RETURN, STRIP_AUTO);
    ss.secret = ss_secret_box(edit);
    widget_get_text_utf8(edit, ss.text, sizeof ss.text);
    ss.cur = (int)strlen(ss.text);
    int digits = ss.cur > 0;
    for (int i = 0; i < ss.cur; ++i)
        digits &=
            (ss.text[i] >= '0' && ss.text[i] <= '9') || ss.text[i] == '.' || ss.text[i] == '-';
    ss.set = digits ? SS_SET_DIGITS : SS_SET_LOWER;
    ss.stop = 1;
    ss_sheet(title && *title ? title : ss.secret ? "Wi-Fi Password" : "Text");
    const char *label = widget_get_prop_str(edit, "_strip_label", ""), *tips;
    int form = SS_FORM_CELL;
    if (!tk_strcmp(widget_get_prop_str(window_of(edit), "name", ""), "editwifi_dialog") ||
        ss.secret == SS_SECRET_WIFI)
        form = SS_FORM_FIELD, label = ss.secret ? "Password" : "Network Name";
    else if (!label || !*label)
        form = SS_FORM_QUERY,
        label = *(tips = widget_get_prop_str(edit, "tips", "")) ? tips : "Search";
    tk_snprintf(ss_box_label, sizeof ss_box_label, "%s", label);
    ss.label = ss_box_label;
    void *win = window_of(edit);
    if (ss_wifi_dialog(win)) ss_band_clear(win, top);
    ss_place(win, form, edit);
    bar_title_sync(kb);
    widget_invalidate_force(kb, (void *)0);
    return 1;
}

/* A text the payload keeps itself (the Bluetooth name, bt_name.c): the strip on a page of its own
 * titled title, text prefilled and the cursor at its end, the text in the row the wheel is on
 * (ss_place): as a cell after the row's label (search_strip_label), else the row's whole width; a
 * password with no label in a field under "Password". Submitting hands the text to done, which
 * closes the page (0) or toasts why not, and a held Return closes it with nothing changed. */
/* The next search_strip_text's note: who sees the text (Bluetooth's "Phones and cars see this
 * name"), in 13 px Text Tertiary under the text's box. */
static const char *strip_note __attribute__((section(".scratch")));
void search_strip_note(const char *note) { strip_note = note; }
/* The next search_strip_text's text is a password (the Guest Play hotspot's): dots, the character
 * just typed shown a second, as a password box's. */
static int strip_secret __attribute__((section(".scratch")));
void search_strip_secret(void) { strip_secret = 1; }
/* The next search_strip_text's row's label (Bluetooth's "Name"): the text a cell after it. */
static const char *strip_label __attribute__((section(".scratch")));
void search_strip_label(const char *label) { strip_label = label; }
/* The next search_strip_text's return key's mark, STRIP_* (ss_ret_kind; unset, the arrow). */
static int strip_return __attribute__((section(".scratch")));
void search_strip_return(int kind) { strip_return = kind; }
void search_strip_text(const char *title, const char *text, int max,
                       const char *(*done)(const char *)) {
    const char *note = strip_note, *label = strip_label;
    int secret = strip_secret, ret = strip_return;
    strip_note = 0, strip_secret = 0, strip_label = 0, strip_return = 0;
    if (ss.page || !done || !(ss.page = page_open("searchstrip_page", ss_closed, page_keyup)))
        return;
    ss.box = ss.page, ss.done = done, ss.note = note, ss.ret = ret;
    ss.secret = secret ? SS_SECRET_PASS : SS_SECRET_NONE;
    ss.max = max > 0 && max < SS_TEXT_MAX ? max : SS_TEXT_MAX;
    tk_snprintf(ss.text, (unsigned)ss.max + 1, "%s", text ? text : "");
    ss.cur = (int)strlen(ss.text);
    ss.stop = 1;
    ss.label = label ? label : secret ? "Password" : (const char *)0;
    ss_sheet(title);
    ss_place(window_under(window_manager(), ss.page),
             label    ? SS_FORM_CELL
             : secret ? SS_FORM_FIELD
                      : SS_FORM_ROW,
             0);
}
