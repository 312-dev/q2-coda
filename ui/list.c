/* The list every menu is (docs/oled-design.md#list-row, #selection-bar, #song-rows): rows of one
 * height from a source, a selection, and the motion between two states of them.
 *
 * What moves, each by the frame's clock on a spring that keeps its speed across a new target:
 *  - the scroll: a tick still moves the selection a whole row, and when that takes the list a row
 *    on, the rows glide there (GLIDE_S), sub-pixel, the incoming row sliding into view; a tick
 *    mid-glide retargets from where the list is, so a fast spin keeps gliding;
 *  - the platter (the selection's bar), which slides from row to row and never jumps;
 *  - the lean: while the finger turns the wheel short of its next tick the platter leans toward
 *    the row it is heading for, tilts as a tvOS card does, grows and gleams, and a tick kicks it
 *    the same way; it springs back when the finger lets go;
 *  - the press: Centre sinks the platter and brightens it, the release springs it back;
 *  - a captioned row's grow: the selected row, when it has a caption (ui_row.caption), grows over
 *    CAP_US to hold it under its title in the platter, the rows below it moving down as it does,
 *    and the row the selection left shrinks back the same way (docs/oled-design.md#list-row).
 *
 * Layers, bottom up (docs/ui-rewrite.md#layers): the rows in their resting inks, clipped to the
 * list's own frame and faded at its top and foot; the pinned rows (Songs' Shuffle), which the
 * rest leave under, fading out in the first pixels below them so no row shows through one; the scroll indicator; then the focus layer, drawn last and clipped by
 * nothing but the screen: the platter's shadow, the platter, and over it the contents of
 * whatever rows it covers in their focused inks. So a row beside the focused one, a pinned row
 * and the fades never cut the platter's lean, growth or shadow, and the contents on the platter
 * move with it, a little further than it does (CONTENT_DEPTH, ICON_DEPTH), as layers at
 * different depths.
 *
 * State and drawing are apart: an event or the step changes the state and marks rects; list_draw
 * draws whatever lies in a rect from the state alone. A row in sight keeps its laid-out text
 * (rowc), so drawing it again measures nothing. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "../patch/wheel_accel.h"
#include "px.h"
#include "q2ui.h"

#define LIST_TOP 58  /* where the first row rests, under the status row */
#define LIST_END 282 /* whole rows end here; the rows below fade into the screen's foot */
#define ROW_H 32
#define TEXT_X 24
#define TEXT_MARGIN 2 /* a label's own margin (patch/navigation.h HSCROLL_MARGIN) */
#define RIGHT_COL (SCREEN_W - 28)
#define ICON_PX 20
#define ICON_TEXT_X 52 /* a row's text after its leading glyph */
#define LEAD_END 41    /* a track number ends here, */
#define LEAD_TEXT_X 48 /* and its title starts here */
#define ART_PX 40
#define ART_TEXT_X 70
#define TITLE_PX TYPE(20)
#define META_PX TYPE(15)
#define META_DY 2      /* the small text's box below the title's (song_rows.c SONG_SMALL_DY) */
#define DETAIL_MIN 40  /* a detail with less room than this is dropped (SONG_ARTIST_MIN) */
#define VALUE_GAP 16   /* between a title and the value after it (KV_GAP) */
#define MARK_STEP 28   /* a right-column glyph and the gap after it (patch/wifi_rows.c WIFI_GLYPH + WIFI_GLYPH_GAP) */
#define SEL_INSET 16
#define SEL_R 8
#define RAISE 0.02f       /* the platter at rest is this much larger than its row */
#define LEAN_PX 3.0f      /* its lean toward the row it is heading for, at most */
#define LEAN_GROW 0.035f  /* and its growth at a full lean */
#define LEAN_TILT 0.11f   /* and its tilt, radians */
#define CONTENT_DEPTH 1.4f /* the text on the platter moves this many times the platter's lean, */
#define ICON_DEPTH 1.8f    /* a glyph or art this many: nearer the eye */
#define PRESS_SINK 0.055f /* Centre sinks the platter from 1.02 to 0.965 */
#define GLIDE_S 0.20f     /* the scroll's spring: at rest in about this long */
#define BAR_S 0.15f       /* the platter's, from row to row */
#define LEAN_S 0.22f      /* the lean's way back */
#define LEAN_DAMP 0.78f   /* a little past rest and back, once */
#define KICK 160.0f       /* px/s a tick throws the lean: about 2.5 px before it turns back */
#define PRESS_S 0.28f
#define SB_X 366 /* the indicator: 4 px wide, 5 px from the edge, y 134 to 206 */
#define SB_W 4
#define SB_TOP 134
#define SB_BOTTOM 206
#define SB_MIN 8
#define SB_SHOW_US 1000000u /* shown this long after the last tick (patch/ui_kit.h UI_SHOW_MS), */
#define SB_FADE_US 200000u  /* the last of it fading */
#define FOOT_KEEP 182       /* of 255: how bright a row is just past the list end, */
#define FOOT_END 311        /* fading to nothing by this row of the screen (measured from demo's) */
#define TOP_CLEAR 14        /* rows under the status row are whole this far above the list top, */
#define TOP_BLACK 8         /* and gone by this row of the screen */
#define PIN_FADE 6          /* rows leaving under a pinned row fade over this much of the slot below it */
#define MQ_REST_US 1500000u /* a cut title on the platter waits this long, */
#define MQ_SPEED 35.0f      /* then slides at this many px a second, */
#define MQ_END_US 1500000u  /* and waits at its end before going back */
#define MQ_FADE 12
#define SLIDER_X 190       /* an inline slider's track starts here (docs/oled-design.md#inline-sliders), */
#define SLIDER_END 62      /* and ends this far short of the right column, where its value ends */
#define SLIDER_TRACK_ON 0x7da2ea /* its track on the platter, under a white fill */
#define CACHE 32 /* rows whose text is kept: a power of two, more than a fast glide shows */
/* A captioned row's caption (patch/settings_pages.c, its CAP_* values): Inter 13 from the title's column, CAP_INSET
 * past the platter's start, wrapped at words to the platter's end less the same, at most CAP_LINES lines, CAP_LINE a
 * line and CAP_FOOT under the last; the row grows to it over CAP_US, eased out, at once with no motion. */
#define CAP_PX TYPE(13)
#define CAP_LINES 3
#define CAP_LINE 17
#define CAP_FOOT 5
#define CAP_INSET 8
#define CAP_US 150000u
/* A row editor's (docs/oled-design.md#edit-library, patch/library_edit.c and list_marks.c): the check circle CIRCLE_D
 * across centred at CIRCLE_X, its ring CIRCLE_RING wide, the words from ICON_TEXT_X; the grip's three GRIP_W x GRIP_H
 * bars GRIP_GAP apart, centred where the chevron is; the lifted row's card in the platter's box, LIFT_GROW of a row
 * tall with LIFT_R corners, its ring and shadow; the rows a moved row passes slide aside over SLIDE_US, cubic out. */
#define CIRCLE_X 34
#define CIRCLE_D 16.0f
#define CIRCLE_RING 1.5f
#define GRIP_W 18
#define GRIP_H 2
#define GRIP_GAP 5
#define LIFT_GROW 1.08f
#define LIFT_R 9
#define LIFT_RING 0x141414
#define SLIDE_US 150000u
#define SLIDES 8
/* The A-Z index (docs/oled-design.md#a-z-index), on a list sorted by title (ui_source.letters) of more than AZ_ROWS
 * rows, shown by the wheel spun fast (a tick of two rows or more, wheel_accel): the folded column of letters centred
 * at AZ_X from AZ_TOP to AZ_BOTTOM, the selected row's letter lit on it, and the bubble beside it while the spin goes
 * on; gone AZ_SHOW_US after the last fast tick, its last AZ_FADE_US fading. While it shows the right column moves in
 * AZ_IN px and the platter ends AZ_BAR_IN px sooner, and the scroll indicator stays hidden. */
#define AZ_ROWS 50
#define AZ_X 362
#define AZ_TOP 80
#define AZ_BOTTOM 240
#define AZ_SHOW_US 1000000u
#define AZ_FADE_US 200000u
#define AZ_BUBBLE_US 250000u /* the bubble stays while fast ticks come this close */
#define AZ_IN 4
#define AZ_BAR_IN 8
#define AZ_DISC 16
#define AZ_BUBBLE_W 52
#define AZ_BUBBLE_H 48
#define AZ_BUBBLE_GAP 18 /* the bubble ends this far left of the column, */
#define AZ_POINTER 7     /* its pointer this long towards it */

int list_motion = 1;

struct rowc {
    int index; /* -1: empty */
    unsigned pictures, everything; /* list_pictures and list_rows_changed's count when the row was asked for */
    ui_row row;
    run_t title, detail, value, sub, lead, length;
    float title_x, detail_x, text_end; /* where the title's column ends */
    float bars_right; /* the playing song's bars end here: the right column's, or before a length (B) */
    int az_in; /* the A-Z index's move of the right column when it was laid out */
    int sel; /* Song Artist's Selected Row: whether it was the selection when laid out (-1 not asked) */
};

struct ui_list {
    ui_source src;
    int n, sel, top; /* top: the first row of the scrolling part at rest */
    rect_t box;
    int row_h, rows, y0; /* whole scrolling rows in sight; where the first of them rests */
    spring_t scroll, bar, lean, press, knob;
    unsigned everything_seen; /* list_rows_changed's count when this list last read its rows again */
    float tug;
    int pressed, moving;
    int grab; /* the ROW_SLIDER row the wheel sets, -1 none */
    uint64_t t, press_at, sb_at, mq_at;
    int sb_alpha;
    float mq_x;
    run_t mq_run;
    int mq_row;
    rect_t focus_was; /* the focus layer's box as last marked */
    struct rowc rows_c[CACHE];
    wheel_accel_t accel;
    int playing_seen; /* the playing song's id as the rows were last marked (src.song), 0 none */
    uint64_t az_at; /* the A-Z index: the last fast tick, 0 hidden */
    int az_in, az_letter; /* the right column's move while it shows; the lit letter, 0 '#', 1 to 26 A to Z */
    /* captions: the row growing to its caption (-1 none) and its lines; the row shrinking back and how tall its
     * caption was; the change's start and its share done, 0 to 1 */
    int cap_open, cap_close, cap_lines;
    float cap_close_px, cap_k;
    uint64_t cap_at;
    run_t cap_run[CAP_LINES];
    /* a row editor's: the selected row lifted, its words in Inter Medium; the rows sliding aside, each from its start
     * offset to its place */
    int lifted;
    run_t lift_run;
    struct slide {
        int row;
        float from;
        uint64_t at;
    } slides[SLIDES];
};

#define SONG_COL_GAP 12   /* a song row's columns apart (patch/navigation.h) */
#define PLAY_BARS_W 16    /* the playing song's four bars' ink */
#define PLAY_TITLE_MIN 80 /* the least room B leaves the title beside the bars and the length (song_rows.c) */
#define PLAYING_W 28.5f /* the level bars' 16.5 px and SONG_COL_GAP's 12 before them */
/* The playing song's id, playing or paused, 0 for none (ui/now.c: demo's file's id=, the song's row) */
static int playing_id(void) {
    const ui_now *n = now_get();
    return (n->playing || n->paused) && n->id > 0 ? n->id : 0;
}
/* Row i as the source says, marked as the playing song's (docs/oled-design.md, the playing song's row:
 * its title accent, the level bars on the right) when it is that song, known by its id */
static void get_row(ui_list *l, int i, ui_row *r) {
    l->src.row(l->src.ctx, i, r);
    int id = l->src.song ? playing_id() : 0;
    if (id && l->src.song(l->src.ctx, i) == id) r->flags |= ROW_PLAYING;
}
static int selectable(ui_list *l, int i) {
    ui_row r = { 0 };
    get_row(l, i, &r);
    return !(r.flags & (ROW_GREY | ROW_HEADER));
}
/* How far row i reaches below its own height now: the caption it is growing to or shrinking from. */
static float cap_full(const ui_list *l) { return l->cap_lines ? (float)(l->cap_lines * CAP_LINE + CAP_FOOT) : 0; }
static float row_extra(const ui_list *l, int i) {
    return i == l->cap_open ? cap_full(l) * l->cap_k : i == l->cap_close ? l->cap_close_px * (1 - l->cap_k) : 0;
}
/* the rows above i that have grown push it down */
static float extra_above(const ui_list *l, int i) {
    float e = 0;
    if (l->cap_open >= 0 && l->cap_open < i) e += row_extra(l, l->cap_open);
    if (l->cap_close >= 0 && l->cap_close < i && l->cap_close != l->cap_open) e += row_extra(l, l->cap_close);
    return e;
}
/* how far row i still is from its place, sliding aside */
static float slide_of(const ui_list *l, int i) {
    for (int k = 0; k < SLIDES; ++k) {
        const struct slide *sl = &l->slides[k];
        if (!sl->at || sl->row != i) continue;
        float t = clampf((float)(ui_time() - sl->at) / (float)SLIDE_US, 0, 1);
        return sl->from * (1 - ease_cubic_out(t));
    }
    return 0;
}
static float row_y(const ui_list *l, int i) {
    if (i < l->src.pinned) return (float)(l->box.y + i * l->row_h) + extra_above(l, i) + slide_of(l, i);
    return (float)((double)(l->y0 + (i - l->src.pinned) * l->row_h) - l->scroll.x) + extra_above(l, i) + slide_of(l, i);
}
static float bar_to(const ui_list *l) {
    if (l->sel < l->src.pinned) return (float)(l->box.y + l->sel * l->row_h);
    return (float)(l->y0 + (l->sel - l->top) * l->row_h);
}
static rect_t frame_rect(const ui_list *l) {
    /* a list under the status row runs on under it to the screen's top and down to its foot */
    if (l->box.y == LIST_TOP) return (rect_t){ l->box.x, 0, l->box.w, SCREEN_H };
    /* one that names where its rows fade (Home's) runs on to the screen's foot, the row past its end fading there */
    if (l->src.fade_to) return (rect_t){ l->box.x, l->box.y, l->box.w, SCREEN_H - l->box.y };
    return l->box;
}
static rect_t sb_rect(void) { return (rect_t){ SB_X, SB_TOP, SB_W, SB_BOTTOM - SB_TOP }; }

/* The platter now: its row's box grown by the raise, the lean and the press, moved by the lean. */
static rectf_t platter(const ui_list *l, float *tilt, float *lean_share) {
    float share = clampf((float)l->lean.x / LEAN_PX, -1.5f, 1.5f);
    float scale = 1 + RAISE + LEAN_GROW * fabsf(share) + (float)l->press.x;
    float bw = (float)(l->box.w - 2 * SEL_INSET - (l->az_in ? AZ_BAR_IN : 0));
    float w = bw * scale, h = ((float)l->row_h + row_extra(l, l->sel)) * scale;
    float cx = (float)(l->box.x + SEL_INSET) + bw / 2;
    float cy = (float)l->bar.x + ((float)l->row_h + row_extra(l, l->sel)) / 2 + share * LEAN_PX;
    if (tilt) *tilt = -share * LEAN_TILT;
    if (lean_share) *lean_share = share;
    return (rectf_t){ cx - w / 2, cy - h / 2, w, h };
}
/* everything the focus layer can touch: the platter and its shadow */
static ui_list *traced;

/* The living row (docs/oled-design.md#selection-bar; the owner's, 2026-11-27, "i like subtle, ship it"): red cells
 * drifting through the selected row's platter on a 70 bpm lub-dub, from ui/cells.c, a function of a clock of its own.
 * The clock runs while the list is lit, eases to a stop over LIVE_EASE_US once nothing has been pressed or turned for
 * LIVE_IDLE_US, and starts again on the next input; it stands still under Reduce Motion (the cells shown as they
 * were) and in the dark. The platter is drawn with them whenever its colour leaves room for venous: every channel of
 * LIVE_VENOUS at or under the platter's, so Arterial has them and Blue does not. A lifted row's card has none.
 * --living 0 draws none (test/ui.py's other checks read the plain platter); --living-fps is the frame rate while only
 * the cells move (60: the owner's "no visual sacrifice"; 30 reads coarser and costs half). */
int list_living = 1, list_living_fps = 60;
#define LIVE_IDLE_US 20000000ull    /* still this long and the cells come to rest */
#define LIVE_EASE_US 1500000ull     /* over this, eased out */
#define LIVE_VENOUS 0x5c060e        /* the colour a cell's rim pulls toward */
#define LIVE_STRENGTH 13107         /* how far a near cell's rim goes toward it, Q16: subtle, 0.20 */
#define LIVE_CATCH 131072           /* the sheen's catch, Q16: a cell under the gleam is up to 3 times as dark */
static struct {
    cells_t mask;
    cells_ink_t ink;
    struct cells_paint paint;
    uint32_t ink_rgb;      /* the platter the ink was made for, its panel's bits beside it */
    unsigned ink_bits;
    int w, h;              /* the mask's box */
    uint64_t drawn, t, input; /* the clock the mask was made at; the clock's last step; the last input */
    double clock;          /* the cells' own time, us */
} live = { .drawn = ~0ull };
/* whether rgb, a platter's colour, takes cells */
static int live_takes(uint32_t rgb) {
    for (int s = 0; s < 24; s += 8)
        if ((LIVE_VENOUS >> s & 0xff) > (rgb >> s & 0xff)) return 0;
    return 1;
}
static rect_t focus_rect(const ui_list *l) { return rect_of(platter(l, 0, 0), l->lifted ? 14 : 10); }

static void settle(ui_list *l) {
    spring_set(&l->scroll, l->scroll.to), spring_set(&l->bar, l->bar.to);
    spring_set(&l->lean, l->lean.to), spring_set(&l->press, l->press.to);
    spring_set(&l->knob, l->knob.to);
}
/* A motion starts from rest: its first frame is drawn for the refresh that will show it. */
static void kick(ui_list *l) {
    if (!l->moving) l->t = ui_time() - fb_refresh_us();
    l->moving = 1;
    ui_again();
}

static void retarget(ui_list *l) {
    int p = l->src.pinned;
    /* on a pinned row the rows under it are at their top: the wheel reaches a pinned row from the
     * first of them, whether it stopped there on the way or a fast turn passed over it */
    if (l->top < p || l->sel < p) l->top = p;
    /* a caption the selected row grows to takes the whole rows it needs under it, so its foot stays on or above the
     * list's end (patch/settings_pages.c cap_step, nav/lists.c list_grown_foot) */
    int under = l->sel == l->cap_open ? ((int)cap_full(l) + l->row_h - 1) / l->row_h : 0;
    if (l->sel >= p) {
        if (l->sel < l->top) l->top = l->sel;
        if (l->sel + under >= l->top + l->rows) l->top = l->sel + under - l->rows + 1;
    }
    int last = l->n - l->rows;
    if (l->sel + under - l->rows + 1 > last) last = l->sel + under - l->rows + 1;
    if (l->top > last) l->top = last > p ? last : p;
    l->scroll.to = (double)((l->top - p) * l->row_h);
    l->bar.to = bar_to(l);
}

static unsigned row_text_flags(ui_list *l, int i) {
    ui_row r = { 0 };
    get_row(l, i, &r);
    return r.flags;
}
static void forget(ui_list *l) {
    for (int i = 0; i < CACHE; ++i) l->rows_c[i].index = -1;
    l->mq_row = -1;
}

/* Bumped by list_pictures_changed and list_rows_changed (below) */
static unsigned pictures, everything;
static struct rowc *row_text(ui_list *l, int i);
static void moved(ui_list *l, int old);

/* The selected row's caption, if it has one, laid out in lines: greedy at spaces, a word wider than the line cut with
 * an ellipsis there (patch/settings_pages.c caption_paint). */
static void cap_layout(ui_list *l, const char *text) {
    int w = l->box.w - 2 * SEL_INSET - 2 * CAP_INSET;
    const char *at = text;
    l->cap_lines = 0;
    while (*at && l->cap_lines < CAP_LINES) {
        char line[192];
        size_t best = 0, end = 0, n = strlen(at);
        while (end < n) {
            size_t e = end + 1;
            while (e < n && at[e] != ' ') ++e;
            if (e >= sizeof line) break;
            memcpy(line, at, e), line[e] = 0;
            if (best && text_width(FACE_REGULAR, CAP_PX, line) > (float)w) break;
            best = end = e;
        }
        if (!best) best = n < sizeof line ? n : sizeof line - 1;
        memcpy(line, at, best), line[best] = 0;
        run_make(&l->cap_run[l->cap_lines++], FACE_REGULAR, CAP_PX, line, w);
        at += best;
        while (*at == ' ') ++at;
    }
}
/* The selection's row grows to its caption and the row it left shrinks from its own; with no motion both are at their
 * ends at once. Before retarget, which keeps the grown row's foot in sight. */
static void cap_to(ui_list *l, int animate) {
    int open = -1;
    if (l->n) {
        struct rowc *c = row_text(l, l->sel);
        if (c->row.caption && *c->row.caption) open = l->sel;
        if (open >= 0 && open == l->cap_open) return;
        if (open < 0 && l->cap_open < 0) return;
        l->cap_close_px = l->cap_open >= 0 ? row_extra(l, l->cap_open) : 0;
        l->cap_close = l->cap_open;
        l->cap_open = open, l->cap_lines = 0;
        if (open >= 0) cap_layout(l, c->row.caption);
    } else
        l->cap_open = l->cap_close = -1, l->cap_lines = 0;
    l->cap_at = ui_time(), l->cap_k = 0;
    if (!animate || !list_motion || ui_reduce_motion) l->cap_k = 1, l->cap_close = -1;
}

ui_list *list_new(const ui_source *src) {
    ui_list *l = calloc(1, sizeof *l);
    l->src = *src;
    l->box = src->box.w ? src->box : (rect_t){ 0, LIST_TOP, SCREEN_W, LIST_END - LIST_TOP };
    l->row_h = src->row_h ? src->row_h : ROW_H;
    l->y0 = l->box.y + src->pinned * l->row_h;
    l->rows = (l->box.y + l->box.h - l->y0) / l->row_h;
    l->n = src->count(src->ctx);
    l->grab = -1;
    l->cap_open = l->cap_close = -1, l->cap_k = 1;
    live.input = ui_time(); /* a list that opens is looked at */
    forget(l);
    /* a list opens on its first row that scrolls: a pinned row (Shuffle) is reached by the wheel */
    l->sel = src->pinned < l->n ? src->pinned : 0;
    while (l->sel < l->n - 1 && !selectable(l, l->sel)) ++l->sel;
    cap_to(l, 0);
    retarget(l);
    if (l->n) l->knob.to = row_text_flags(l, l->sel) & ROW_ON ? 1 : 0;
    l->everything_seen = everything;
    settle(l);
    l->focus_was = focus_rect(l);
    ui_mark(frame_rect(l));
    return l;
}
void list_free(ui_list *l) {
    if (traced == l) traced = 0;
    free(l);
}
int list_selected(const ui_list *l) { return l->sel; }
int list_grabbed(const ui_list *l) { return l->grab; }
int list_set_down(ui_list *l) {
    if (l->grab < 0) return 0;
    l->grab = -1;
    ui_mark(l->focus_was);
    return 1;
}
float list_scroll(const ui_list *l) { return (float)l->scroll.x; }

void list_lift(ui_list *l, int on) {
    l->lifted = on && l->n;
    if (l->lifted) {
        struct rowc *c = row_text(l, l->sel);
        run_make(&l->lift_run, FACE_MEDIUM, TITLE_PX, c->row.title ? c->row.title : "", (int)(c->text_end - c->title_x));
    }
    ui_mark(frame_rect(l)), ui_mark(l->focus_was);
}
int list_lifted(const ui_list *l) { return l->lifted; }
/* The source moved row from to to: the selection goes with it at once (the card glides there as the platter does), and
 * each row between, now a row the other way, slides from where it showed to its place; a row still sliding from an
 * earlier move keeps where it is and slides on from there. */
void list_moved_row(ui_list *l, int from, int to) {
    if (from == to || from < 0 || to < 0 || from >= l->n || to >= l->n) return;
    int dir = to > from ? 1 : -1;
    float shown[SLIDES];
    for (int k = 0; k < SLIDES; ++k) { /* where each slide is now, then its row's new number */
        struct slide *sl = &l->slides[k];
        shown[k] = sl->at ? slide_of(l, sl->row) : 0;
        if (!sl->at) continue;
        if (sl->row == from) sl->row = to;
        else if ((sl->row - from) * dir > 0 && (sl->row - to) * dir <= 0) sl->row -= dir;
    }
    uint64_t now = ui_time();
    int motion = list_motion && !ui_reduce_motion;
    for (int i = from; i != to; i += dir) { /* the rows passed, now from to to less one, each a row the other way */
        int k = 0, free = -1;
        for (; k < SLIDES && !(l->slides[k].at && l->slides[k].row == i); ++k)
            if (free < 0 && (!l->slides[k].at || now - l->slides[k].at >= SLIDE_US)) free = k;
        float from_y = (k < SLIDES ? shown[k] : 0) + (float)(dir * l->row_h);
        if (k == SLIDES) k = free;
        if (k < 0) continue;
        l->slides[k] = (struct slide){ i, motion ? from_y : 0, motion ? now : 0 };
    }
    for (int k = 0; k < SLIDES; ++k)
        if (l->slides[k].at && l->slides[k].row == to) l->slides[k].at = 0; /* the moved row goes at once */
    forget(l);
    int old = l->sel;
    l->sel = to;
    moved(l, old);
    if (l->lifted) list_lift(l, 1);
    ui_mark(frame_rect(l));
    if (motion) kick(l);
}

/* Rows changed in place (a switch flipped, a value cycled): the same rows are read again, and the
 * selected row's switch slides to its new side. */
void list_reload(ui_list *l) {
    int on = 0;
    forget(l);
    if (l->n) on = row_text_flags(l, l->sel) & ROW_ON;
    l->knob.to = on ? 1 : 0;
    cap_to(l, 1); /* a row's caption may have come, gone or changed with it */
    if (l->cap_open >= 0) cap_layout(l, row_text(l, l->sel)->row.caption);
    retarget(l);
    if (!list_motion) spring_set(&l->knob, l->knob.to);
    else kick(l);
    ui_mark(frame_rect(l));
}

void list_changed(ui_list *l) {
    l->n = l->src.count(l->src.ctx);
    forget(l);
    if (l->sel >= l->n) l->sel = l->n ? l->n - 1 : 0;
    l->cap_open = l->cap_close = -1, cap_to(l, 0);
    retarget(l);
    settle(l);
    ui_mark(frame_rect(l));
    ui_mark(l->focus_was);
}

/* Row i's text, laid out once while the row stays in sight. The title keeps what it needs of the
 * room; a detail (a song's artist) takes what is left after two spaces, cut with an ellipsis, or
 * is dropped; a value keeps its width at the right column and the title is cut to leave it. */
/* Bumped by list_pictures_changed: a row's art is asked for again, as the picture it was given
 * may have been replaced (a cover read) or freed. */
void list_pictures_changed(void) { ++pictures; }
void list_rows_changed(void) { ++everything; }

static struct rowc *row_text(ui_list *l, int i) {
    struct rowc *c = &l->rows_c[i & (CACHE - 1)];
    if (c->index == i && !run_stale(&c->title) && !run_stale(&c->detail) && !run_stale(&c->value) &&
        !run_stale(&c->sub) && !run_stale(&c->lead) && !run_stale(&c->length) && (!c->row.art || c->pictures == pictures) &&
        c->everything == everything && c->az_in == l->az_in && (c->sel < 0 || c->sel == (i == l->sel)))
        return c;
    static run_t gap;
    if (!gap.n || run_stale(&gap)) run_make(&gap, FACE_REGULAR, TITLE_PX, "  ", -1);
    ui_row *r = &c->row;
    memset(r, 0, sizeof *r);
    get_row(l, i, r);
    c->index = i, c->pictures = pictures, c->everything = everything, c->az_in = l->az_in, c->sel = -1;
    /* a song row's artist (detail) and length by the Song Artist and Song Length options (docs/oled-design.md#song-rows) */
    int how = l->src.song && r->detail && *r->detail ? config_song_artist() : SONG_ARTIST_INLINE;
    if (how == SONG_ARTIST_OFF) r->detail = 0;
    if (how == SONG_ARTIST_SELECTED) c->sel = i == l->sel;
    if (how == SONG_ARTIST_SELECTED && !c->sel) r->detail = 0;
    int two = l->row_h > ROW_H && r->sub;
    float x = (float)(l->box.x + TEXT_X + TEXT_MARGIN);
    if (r->icon || r->flags & ROW_CIRCLE) x = (float)(l->box.x + ICON_TEXT_X + TEXT_MARGIN);
    if (r->lead) x = (float)(l->box.x + LEAD_TEXT_X + TEXT_MARGIN);
    if (r->art) x = (float)(l->box.x + ART_TEXT_X + TEXT_MARGIN);
    float end = (float)(l->box.x + l->box.w - (SCREEN_W - RIGHT_COL) - l->az_in) - TEXT_MARGIN;
    c->value.n = c->detail.n = c->sub.n = c->lead.n = c->length.n = 0;
    c->bars_right = end + TEXT_MARGIN;
    c->value.made = c->detail.made = c->sub.made = c->lead.made = c->length.made = c->title.made;
    if (r->flags & ROW_HEADER) {
        run_make(&c->title, FACE_REGULAR, META_PX, r->title ? r->title : "", (int)(end - x));
        c->title_x = x, c->text_end = end;
        c->value.made = c->detail.made = c->sub.made = c->lead.made = c->length.made = c->title.made;
        return c;
    }
    if (r->length && *r->length) { /* right-aligned on the right column; the playing song's bars before it (B) while
                                      the title keeps PLAY_TITLE_MIN beside both, else in its place (A) */
        run_make(&c->length, FACE_REGULAR, META_PX, r->length, -1);
        float column = end + TEXT_MARGIN;
        if (r->flags & ROW_PLAYING && column - c->length.w - 2 * SONG_COL_GAP - PLAY_BARS_W - x < PLAY_TITLE_MIN)
            c->length.n = 0;
        else if (r->flags & ROW_PLAYING)
            c->bars_right = column - c->length.w - SONG_COL_GAP, end = c->bars_right - PLAYING_W;
        else
            end = column - c->length.w - SONG_COL_GAP;
    }
    if (c->length.n) {
    } else if (r->flags & ROW_SLIDER) { /* the title before the track; the value after it, in small text */
        if (r->value) run_make(&c->value, FACE_REGULAR, META_PX, r->value, SLIDER_END - 8);
        end = (float)(l->box.x + SLIDER_X) - VALUE_GAP;
    } else if (r->value && *r->value) {
        if (r->flags & ROW_PLAYING) end -= PLAYING_W; /* the bars on the right column, the value left of them */
        run_make(&c->value, FACE_REGULAR, two ? META_PX : TITLE_PX, r->value, (int)((end - x) / 2));
        end -= c->value.w + VALUE_GAP;
    } else if (r->flags & ROW_SWITCH)
        end -= 40 + VALUE_GAP;
    else if (r->flags & (ROW_CHEVRON | ROW_CHECK | ROW_PLAYING))
        end -= 20;
    else if (r->flags & ROW_GRIP)
        end -= GRIP_W + 6;
    for (int k = 0; k < 3 && r->marks[k]; ++k) end -= MARK_STEP;
    /* Right, and Selected Row on the selection, put the artist on the right column, taking at most half the room
     * from the title (song_rows.c song_fit); a length or the playing song's bars there turn it inline beside them */
    if (!two && r->detail && *r->detail && how != SONG_ARTIST_INLINE && !c->length.n && !(r->flags & ROW_PLAYING) &&
        !(r->value && *r->value)) {
        float room = end - x;
        run_make(&c->title, FACE_REGULAR, TITLE_PX, r->title ? r->title : "", -1);
        run_make(&c->detail, FACE_REGULAR, META_PX, r->detail, -1);
        float a = room - gap.w - (c->title.w < room / 2 ? c->title.w : room / 2);
        if (a > c->detail.w) a = c->detail.w;
        if (a < DETAIL_MIN) c->detail.n = 0, a = 0;
        else run_make(&c->detail, FACE_REGULAR, META_PX, r->detail, (int)a);
        run_make(&c->title, FACE_REGULAR, TITLE_PX, r->title ? r->title : "", (int)(a ? room - a - gap.w : room));
        c->title_x = x, c->text_end = a ? end - a - gap.w : end;
        c->detail_x = end - c->detail.w;
    } else {
        run_make(&c->title, FACE_REGULAR, TITLE_PX, r->title ? r->title : "", (int)(end - x));
        c->title_x = x, c->text_end = end;
        float left = end - x - c->title.w - gap.w;
        if (!two && r->detail && *r->detail && left >= DETAIL_MIN) {
            run_make(&c->detail, FACE_REGULAR, META_PX, r->detail, (int)left);
            c->detail_x = x + c->title.w + gap.w;
        }
    }
    if (two) run_make(&c->sub, FACE_REGULAR, META_PX, r->sub, (int)(end - x));
    if (r->lead) run_make(&c->lead, FACE_REGULAR, META_PX, r->lead, -1);
    c->value.made = c->detail.made = c->sub.made = c->lead.made = c->length.made = c->title.made;
    return c;
}

/* The marks at a row's right column. */
static void chevron(float cx, float cy, uint32_t rgb) {
    stroke_line(cx - 2.5f, cy - 5, cx + 2.5f, cy, 2, rgb, 1);
    stroke_line(cx + 2.5f, cy, cx - 2.5f, cy + 5, 2, rgb, 1);
}
static void check(float cx, float cy, uint32_t rgb) {
    stroke_line(cx - 5, cy + 0.5f, cx - 1.5f, cy + 4, 2, rgb, 1);
    stroke_line(cx - 1.5f, cy + 4, cx + 5.5f, cy - 4, 2, rgb, 1);
}
static void grip(float cx, float cy, uint32_t rgb) {
    for (int k = -1; k <= 1; ++k) fill_round((rectf_t){ cx - GRIP_W / 2.0f, cy + (float)(k * GRIP_GAP) - 1, GRIP_W, GRIP_H }, 0, 0, rgb, 1, 0);
}
/* A row editor's check circle (patch/list_marks.c check_circle): shown, the accent's disc with a white check (on the
 * selection a white disc, the accent's check); hidden, a ring in Text Tertiary (white on the selection); locked, an Off
 * disc with a padlock in Text Secondary. */
static void circle(float cx, float cy, unsigned flags, int focused) {
    float r = CIRCLE_D / 2;
    if (flags & ROW_LOCK) {
        fill_disc(cx, cy, r, tok(TOK_OFF), 1);
        uint32_t ink = tok(TOK_SECONDARY);
        fill_round((rectf_t){ cx - 3.5f, cy - 1, 7, 5.5f }, 1, 0, ink, 1, 0); /* its body, */
        stroke_line(cx - 2, cy - 1, cx - 2, cy - 3, 1.3f, ink, 1);          /* and its shackle */
        stroke_line(cx + 2, cy - 1, cx + 2, cy - 3, 1.3f, ink, 1);
        stroke_line(cx - 2, cy - 3, cx, cy - 4.6f, 1.3f, ink, 1);
        stroke_line(cx, cy - 4.6f, cx + 2, cy - 3, 1.3f, ink, 1);
        return;
    }
    if (!(flags & ROW_ON)) {
        uint32_t ink = focused ? 0xffffff : tok(TOK_TERTIARY);
        fill_disc(cx, cy, r, ink, 1);
        fill_disc(cx, cy, r - CIRCLE_RING, focused ? RGB_ACCENT : tok(TOK_CANVAS), 1);
        return;
    }
    uint32_t disc = focused ? 0xffffff : RGB_ACCENT, mark = focused ? RGB_ACCENT : 0xffffff;
    fill_disc(cx, cy, r, disc, 1);
    stroke_line(cx - 3.6f, cy + 0.3f, cx - 1.1f, cy + 2.8f, 1.8f, mark, 1);
    stroke_line(cx - 1.1f, cy + 2.8f, cx + 3.9f, cy - 2.8f, 1.8f, mark, 1);
}
static void level_bars(float right, float cy, uint32_t rgb) {
    static const float high[] = { 8, 12, 5, 10 };
    for (int k = 0; k < 4; ++k) {
        float x = right - 16.5f + (float)k * 4.5f;
        fill_round((rectf_t){ x, cy - high[k] / 2, 3, high[k] }, 1.5f, 0, rgb, 1, 0);
    }
}
/* A 40 x 24 switch ending at `right`, its knob at share `on` of its travel
 * (docs/oled-design.md#list-row). */
static void toggle(float right, float cy, float on, int focused) {
    rectf_t track = { right - 40, cy - 12, 40, 24 };
    float kx = track.x + 12 + 16 * on;
    if (!focused) {
        fill_round(track, 12, 0, rgb_mix(tok(TOK_TRACK), RGB_ACCENT, on), 1, 0);
        fill_disc(kx, cy, 10, 0xffffff, 1);
        return;
    }
    /* on the platter: on is a white track with an accent knob, off a white outline round a white
     * knob */
    fill_round(track, 12, 0, 0xffffff, 1, 0);
    if (on < 1)
        fill_round((rectf_t){ track.x + 1.5f, track.y + 1.5f, 37, 21 }, 10.5f, 0, RGB_ACCENT, 1 - on, 0);
    fill_disc(kx, cy, 10, rgb_mix(0xffffff, RGB_ACCENT, on), 1);
}

/* An inline slider's track from x0 to x1 on cy, filled to level with its knob there: Track and Accent
 * Blue in the list, #7DA2EA and white on the platter; the knob grows from 12 to 16 px while grabbed. */
static void slider(float x0, float x1, float cy, float level, int focused, int grabbed) {
    float kx = x0 + (x1 - x0) * clampf(level, 0, 1), kr = grabbed ? 8 : 6;
    fill_round((rectf_t){ x0, cy - 2, x1 - x0, 4 }, 2, 0, focused ? SLIDER_TRACK_ON : tok(TOK_TRACK), 1, 0);
    if (kx > x0) fill_round((rectf_t){ x0, cy - 2, kx - x0, 4 }, 2, 0, focused ? 0xffffff : RGB_ACCENT, 1, 0);
    if (!focused && ui_light) fill_disc(kx, cy, kr + 1, tok(TOK_TRACK), 1); /* white on white: edged, as iOS's thumb */
    fill_disc(kx, cy, kr, 0xffffff, 1);
}

/* Row i's contents with its top at y: in its resting inks, or (focused) as it reads on the
 * platter, its text dy lower and its glyph and art deeper still. */
static void row_paint(ui_list *l, int i, float y, int focused, float dy) {
    struct rowc *c = row_text(l, i);
    const ui_row *r = &c->row;
    float h = (float)l->row_h, cy = y + h / 2, ty = y + dy * CONTENT_DEPTH, iy = dy * ICON_DEPTH;
    uint32_t on = tok(TOK_ON_ACCENT);
    if (r->flags & ROW_HEADER) {
        run_draw(&c->title, c->title_x, y + h - (float)META_PX - 4, tok(TOK_TERTIARY), 256);
        return;
    }
    int grey = r->flags & (ROW_GREY | ROW_DIM), accent = r->flags & (ROW_ACCENT | ROW_PLAYING);
    int lifted = focused && l->lifted && i == l->sel; /* on its card: the list's inks, the words in Inter Medium */
    if (lifted) on = tok(TOK_PRIMARY);
    uint32_t ink = focused ? on : grey ? tok(TOK_TERTIARY) : accent ? RGB_ACCENT : tok(TOK_PRIMARY);
    uint32_t dim = focused ? on : tok(TOK_TERTIARY);
    uint32_t val = focused ? on : grey ? tok(TOK_TERTIARY) : tok(TOK_SECONDARY);
    if (r->icon)
        image_draw(r->icon, (rectf_t){ (float)(l->box.x + TEXT_X), cy - ICON_PX / 2 + iy, ICON_PX, ICON_PX }, 0, 0,
                   256, focused ? (long)on : accent ? (long)RGB_ACCENT : (long)tok(TOK_SECONDARY));
    if (r->art)
        image_draw(r->art, (rectf_t){ (float)(l->box.x + TEXT_X), y + 6 + iy, ART_PX, ART_PX }, 4, 0, 256, -1);
    if (r->flags & ROW_CIRCLE) circle((float)(l->box.x + CIRCLE_X), cy + iy, r->flags, focused);
    if (c->lead.n) run_draw(&c->lead, (float)(l->box.x + LEAD_END) - c->lead.w, ty + (h - (float)META_PX) / 2 + META_DY, dim, 256);
    int two = c->sub.n > 0;
    float title_y = two ? ty + 5 : ty + (h - (float)TITLE_PX) / 2;
    if (lifted && l->lift_run.n)
        run_draw(&l->lift_run, c->title_x, title_y, ink, 256);
    else if (focused && i == l->mq_row && l->mq_run.n) {
        /* the cut title, whole, sliding in its column with its ends faded */
        fade_set((int)c->title_x, (int)c->text_end, l->mq_x > 0.5f ? MQ_FADE : 0, MQ_FADE);
        run_draw(&l->mq_run, c->title_x - l->mq_x, title_y, ink, 256);
        fade_clear();
    } else
        run_draw(&c->title, c->title_x, title_y, ink, 256);
    if (c->detail.n) run_draw(&c->detail, c->detail_x, ty + (h - (float)META_PX) / 2 + META_DY, dim, 256);
    if (two) run_draw(&c->sub, c->title_x, ty + 29, dim, 256);
    float right = (float)(l->box.x + l->box.w - (SCREEN_W - RIGHT_COL) - l->az_in);
    if (c->length.n) {
        run_draw(&c->length, right - c->length.w, ty + (h - (float)META_PX) / 2 + META_DY, dim, 256);
        if (r->flags & ROW_PLAYING) level_bars(c->bars_right, cy + iy, focused ? on : RGB_ACCENT);
    } else if (r->flags & ROW_SLIDER) {
        slider((float)(l->box.x + SLIDER_X), right - SLIDER_END, cy + iy, r->level, focused, i == l->grab);
        if (c->value.n) run_draw(&c->value, right - c->value.w, ty + (h - (float)META_PX) / 2, val, 256);
    } else if (c->value.n) {
        float vr = r->flags & ROW_PLAYING ? right - PLAYING_W : right; /* a playlist's artist left of the bars, as a length */
        run_draw(&c->value, vr - c->value.w, two ? ty + 5 + 3 : ty + (h - (float)TITLE_PX) / 2, val, 256);
        if (r->flags & ROW_PLAYING) level_bars(right, cy + iy, focused ? on : RGB_ACCENT);
    } else if (r->flags & ROW_SWITCH)
        toggle(right, cy + iy, i == l->sel ? (float)l->knob.x : r->flags & ROW_ON ? 1.0f : 0.0f, focused);
    else if (r->flags & ROW_PLAYING)
        level_bars(right, cy + iy, focused ? on : RGB_ACCENT);
    else if (r->flags & ROW_CHECK)
        check(right - 7, cy + iy, focused ? on : RGB_ACCENT);
    else if (r->flags & ROW_CHEVRON)
        chevron(right - 5, cy + iy, focused ? on : grey ? tok(TOK_TERTIARY) : tok(TOK_CHEVRON));
    else if (r->flags & ROW_GRIP)
        grip(right - 5, cy + iy, focused ? on : tok(TOK_TERTIARY));
    for (int k = 0; k < 3 && r->marks[k]; ++k) { /* Text Primary, white on the platter, as demo's (wifi_mark) */
        char name[48];
        snprintf(name, sizeof name, "%s_d.", r->marks[k]);
        const image_t *im = icon(name);
        float x = right - (float)(ICON_PX + k * MARK_STEP);
        if (im)
            image_draw(im, (rectf_t){ x, cy - ICON_PX / 2 + iy, ICON_PX, ICON_PX }, 0, 0, 256,
                       focused ? (long)on : grey ? (long)tok(TOK_TERTIARY) : r->flags & ROW_SOFT ? (long)tok(TOK_SECONDARY) : (long)tok(TOK_PRIMARY));
    }
}

static void draw_indicator(const ui_list *l) {
    int total = l->n - l->src.pinned;
    if (!l->sb_alpha || total <= l->rows || l->az_at) return;
    float track = SB_BOTTOM - SB_TOP, len = track * (float)l->rows / (float)total;
    if (len < SB_MIN) len = SB_MIN;
    float at = (track - len) * clampf((float)(l->scroll.x / (double)((total - l->rows) * l->row_h)), 0, 1);
    float a = (float)l->sb_alpha / 255;
    fill_round((rectf_t){ SB_X, SB_TOP, SB_W, track }, 2, 0, 0x3a3a3a, a, 0);
    fill_round((rectf_t){ SB_X, SB_TOP + at, SB_W, len }, 2, 0, 0xe5e5e5, a, 0);
}

static void list_draw_rows(ui_list *l, rect_t r) {
    rect_t frame = frame_rect(l), part, rows;
    int p = l->src.pinned, full = l->box.y == LIST_TOP;
    if (!rect_meet(r, frame, &part)) return;
    clip_set(part);
    if (!l->src.bare) fill(part, tok(TOK_CANVAS));
    /* the scrolling rows, under everything: clipped to the list's frame, which under a pinned
     * row starts at that row's foot */
    rows = frame;
    if (p) rows.h -= l->y0 - rows.y, rows.y = l->y0;
    if (rect_meet(part, rows, &rows) && l->n > p) {
        clip_set(rows);
        int first = p + (int)floor((l->scroll.x + (double)(rows.y - l->y0)) / (double)l->row_h);
        first -= l->cap_open >= 0 || l->cap_close >= 0 ? (CAP_LINES * CAP_LINE + CAP_FOOT) / l->row_h + 1 : 0;
        --first; /* a row above sliding aside, down into sight */
        if (first < p) first = p;
        for (int i = first; i < l->n; ++i) {
            float y = row_y(l, i);
            if (y >= (float)(rows.y + rows.h)) break;
            row_paint(l, i, y, 0, 0);
        }
        /* the list's ends: rows fade into the screen's foot, and into its top once scrolled */
        if (!l->src.bare && (full || p || l->src.fade_to)) {
            int foot = l->box.y + l->box.h, end = l->src.fade_to ? l->src.fade_to : FOOT_END;
            fade_rows(foot, end, FOOT_KEEP, 0);
            fill((rect_t){ frame.x, end, frame.w, SCREEN_H - end }, tok(TOK_CANVAS));
        }
        if (p)
            fade_rows(l->y0, l->y0 + PIN_FADE, 0, 255);
        else if (!l->src.bare && full && l->scroll.x > 0) {
            fill((rect_t){ frame.x, 0, frame.w, TOP_BLACK }, tok(TOK_CANVAS));
            fade_rows(TOP_BLACK, LIST_TOP - TOP_CLEAR, 0, 255);
        }
    }
    clip_set(part);
    for (int i = 0; i < p && i < l->n; ++i) row_paint(l, i, row_y(l, i), 0, 0);
    if (rect_meet(part, sb_rect(), 0)) draw_indicator(l);
    /* the focus layer, above all of that and cut by none of it */
    if (!l->n) return;
    float tilt, share;
    rectf_t bar = platter(l, &tilt, &share);
    rect_t on;
    clip_set(r);
    if (!rect_meet(r, focus_rect(l), 0)) return;
    if (l->lifted) { /* a row editor's lifted row on its raised card, in the platter's box, flat, and its contents on it */
        float bh = (float)l->row_h * LIFT_GROW;
        rectf_t card = { bar.x + (bar.w - (float)(l->box.w - 2 * SEL_INSET)) / 2, (float)l->bar.x + ((float)l->row_h - bh) / 2,
                         (float)(l->box.w - 2 * SEL_INSET), bh };
        shadow_round((rectf_t){ card.x, card.y + 6, card.w, card.h }, LIFT_R, 6, 0x000000, 0.55f);
        fill_round((rectf_t){ card.x - 1, card.y - 1, card.w + 2, card.h + 2 }, LIFT_R + 1, 0, ui_light ? 0xebebeb : LIFT_RING, 1, 0);
        fill_round(card, LIFT_R, 0, tok(TOK_RAISED_2), 1, 0);
        if (rect_meet(r, rect_of(card, 0), &on)) clip_set(on), row_paint(l, l->sel, (float)l->bar.x, 1, 0);
        clip_set(r);
        return;
    }
    float pressed = clampf((float)-l->press.x / PRESS_SINK, 0, 1);
    uint32_t blue = rgb_mix(RGB_ACCENT, RGB_ACCENT_PRESSED, pressed);
    shadow_round((rectf_t){ bar.x + 2, bar.y + 3, bar.w - 4, bar.h }, SEL_R, 7, RGB_ACCENT,
                 (0.30f + 0.25f * fabsf(share)) * (1 - pressed));
    gleam_t gleam = { 0, -share * (float)l->row_h * 0.6f, bar.w * 0.45f, (float)l->row_h * 1.1f,
                      0.42f * clampf((fabsf(share) - 0.15f) / 0.85f, 0, 1), 0 };
    int w = (int)ceilf(bar.w), h = (int)ceilf(bar.h);
    if (list_living && list_motion && live_takes(blue) && w <= CELLS_MAX_W && h <= CELLS_MAX_H) {
        if (w != live.w || h != live.h) cells_init(&live.mask, w, h), live.w = w, live.h = h, live.drawn = ~0ull;
        if ((uint64_t)live.clock != live.drawn) live.drawn = (uint64_t)live.clock, cells_step(&live.mask, live.drawn);
        unsigned bits = px_bits_rb << 8 | px_bits_g;
        if (blue != live.ink_rgb || bits != live.ink_bits) {
            cells_ink_init(&live.ink, blue, LIVE_VENOUS, LIVE_STRENGTH, LIVE_CATCH, px_bits_rb, px_bits_g);
            live.ink_rgb = blue, live.ink_bits = bits;
        }
        live.paint = (struct cells_paint){ &live.mask, &live.ink };
        gleam.cells = &live.paint;
    }
    fill_round(bar, SEL_R, tilt, blue, 1, &gleam);
    /* what the platter covers reads in its focused inks, cut to the platter */
    if (!rect_meet(r, rect_of(bar, 0), &on)) return;
    clip_set(on);
    int near = p + (int)floor(((double)(bar.y - (float)l->y0) + l->scroll.x) / (double)l->row_h);
    for (int k = 0; k < p + 7; ++k) {
        int i = k < p ? k : near + k - p - 3;
        if (i < (k < p ? 0 : p) || i >= l->n) continue;
        float y = row_y(l, i);
        if (y >= bar.y + bar.h || y + (float)l->row_h <= bar.y) continue;
        /* how much of the row the platter is over: a row it is leaving moves less with it */
        float over = clampf(1 - fabsf(y - (float)l->bar.x) / (float)l->row_h, 0, 1);
        row_paint(l, i, y, 1, share * LEAN_PX * over);
    }
    /* the selected row's caption under its title, each line once the platter holds it whole */
    if (l->sel == l->cap_open) {
        float y = row_y(l, l->sel) + (float)l->row_h, dy = share * LEAN_PX * CONTENT_DEPTH, foot = y + row_extra(l, l->sel);
        for (int k = 0; k < l->cap_lines && y + (float)((k + 1) * CAP_LINE) <= foot + 0.01f; ++k)
            run_draw(&l->cap_run[k], (float)(l->box.x + SEL_INSET + CAP_INSET), y + (float)(k * CAP_LINE) + (CAP_LINE - CAP_PX) / 2.0f + dy,
                     tok(TOK_CAPTION_ON_ACCENT), 256);
    }
    clip_set(r);
}
/* A row's place in the index: its title's first letter past "The ", "A " or "An ", A to Z as 1 to 26, anything else
 * (digits, symbols, other scripts) 0, '#' */
static int az_of(const char *t) {
    static const char *const skip[] = { "the ", "a ", "an " };
    if (!t) return 0;
    for (int k = 0; k < 3; ++k)
        if (!strncasecmp(t, skip[k], strlen(skip[k])) && t[strlen(skip[k])]) {
            t += strlen(skip[k]);
            break;
        }
    int c = (unsigned char)*t | 0x20;
    return c >= 'a' && c <= 'z' ? c - 'a' + 1 : 0;
}
static float az_alpha(const ui_list *l) {
    uint64_t t = ui_time() - l->az_at;
    return !l->az_at || t >= AZ_SHOW_US ? 0 : t < AZ_SHOW_US - AZ_FADE_US ? 1 : (float)(AZ_SHOW_US - t) / AZ_FADE_US;
}
static rect_t az_rect(void) {
    return (rect_t){ AZ_X - AZ_DISC / 2 - AZ_BUBBLE_GAP - AZ_BUBBLE_W - 2, AZ_TOP - AZ_BUBBLE_H / 2 - 2,
                     AZ_BUBBLE_W + AZ_BUBBLE_GAP + AZ_DISC + 4, AZ_BOTTOM - AZ_TOP + AZ_BUBBLE_H + 4 };
}
static void az_draw(const ui_list *l) {
    static const char folded[] = "#DIMQVZ"; /* seven letters with a dot between each two, as iOS folds it */
    static run_t glyphs[27], big;
    static char big_s[2];
    float a = az_alpha(l);
    if (a <= 0) return;
    uint32_t ink = ui_light ? 0x000000 : 0xffffff, paper = ui_light ? 0xffffff : 0x000000;
    unsigned alpha = (unsigned)(a * 256);
    for (int k = 0; k < 13; ++k) {
        float y = AZ_TOP + (float)k * (AZ_BOTTOM - AZ_TOP) / 12;
        if (k & 1) {
            fill_disc(AZ_X, y, 1.5f, ink, a);
            continue;
        }
        char ch[2] = { folded[k / 2], 0 };
        int g = ch[0] == '#' ? 0 : ch[0] - 'A' + 1;
        if (!glyphs[g].n || run_stale(&glyphs[g])) run_make(&glyphs[g], FACE_MEDIUM, TYPE(13), ch, -1);
        run_draw(&glyphs[g], AZ_X - glyphs[g].w / 2, y - TYPE(13) / 2.0f, ink, alpha);
    }
    /* the lit letter at its own place: the 27 map evenly over the column whether or not it is folded */
    float ly = AZ_TOP + (float)l->az_letter * (AZ_BOTTOM - AZ_TOP) / 26;
    char lit[2] = { l->az_letter ? (char)('A' + l->az_letter - 1) : '#', 0 };
    fill_disc(AZ_X, ly, AZ_DISC / 2.0f, ink, a);
    if (!glyphs[l->az_letter].n || run_stale(&glyphs[l->az_letter])) run_make(&glyphs[l->az_letter], FACE_MEDIUM, TYPE(13), lit, -1);
    run_draw(&glyphs[l->az_letter], AZ_X - glyphs[l->az_letter].w / 2, ly - TYPE(13) / 2.0f, paper, alpha);
    if (ui_time() - l->az_at >= AZ_BUBBLE_US) return;
    /* the bubble while the spin goes on: the letter large, its pointer towards the lit one */
    float bx = AZ_X - AZ_DISC / 2.0f - AZ_BUBBLE_GAP - AZ_BUBBLE_W, by = ly - AZ_BUBBLE_H / 2.0f;
    fill_round((rectf_t){ bx, by, AZ_BUBBLE_W, AZ_BUBBLE_H }, 12, 0, tok(TOK_RAISED_2), a, 0);
    for (int k = 0; k < AZ_POINTER; ++k) /* a triangle as narrowing strokes */
        stroke_line(bx + AZ_BUBBLE_W + (float)k, ly - (float)(AZ_POINTER - k), bx + AZ_BUBBLE_W + (float)k,
                    ly + (float)(AZ_POINTER - k), 1, tok(TOK_RAISED_2), a);
    if (big_s[0] != lit[0] || !big.n || run_stale(&big)) big_s[0] = lit[0], run_make(&big, FACE_MEDIUM, TYPE(22), big_s, -1);
    run_draw(&big, bx + AZ_BUBBLE_W / 2.0f - big.w / 2, ly - TYPE(22) / 2.0f, 0xffffff, alpha);
}

void list_draw(ui_list *l, rect_t r) {
    list_draw_rows(l, r);
    if (l->az_at && rect_meet(r, az_rect(), 0)) {
        clip_set(r);
        az_draw(l);
    }
}

/* The first row at or after i, going dir, whose title starts with another letter than row i's */
static int letter_step(ui_list *l, int i, int dir) {
    ui_row r = { 0 };
    int p = l->src.pinned;
    if (i < p) return dir > 0 ? p : i;
    get_row(l, i, &r);
    int c = (unsigned char)(r.title ? r.title[0] : 0) | 0x20, j = i;
    for (;;) {
        if (j + dir < p || j + dir >= l->n) return j;
        get_row(l, j + dir, &r);
        j += dir;
        if (((unsigned char)(r.title ? r.title[0] : 0) | 0x20) != c) return j;
    }
}

static void moved(ui_list *l, int old) {
    if (l->sel != old) cap_to(l, 1);
    retarget(l);
    l->mq_row = -1, l->mq_at = 0, l->mq_x = 0;
    spring_set(&l->knob, 0);
    if (l->sel != old) {
        struct rowc *c = row_text(l, l->sel);
        spring_set(&l->knob, c->row.flags & ROW_ON ? 1.0f : 0.0f);
        if (c->title.cut) l->mq_at = ui_time(), ui_wake_ms(MQ_REST_US / 1000u);
        if (l->src.focus) l->src.focus(l->src.ctx, l->sel);
    }
    if (!list_motion) {
        double scroll = l->scroll.x;
        settle(l);
        rect_t f = focus_rect(l);
        ui_mark(l->scroll.x != scroll ? frame_rect(l) : rect_union(f, l->focus_was));
        l->focus_was = f;
        return;
    }
    kick(l);
}

void list_select(ui_list *l, int i, int animate) {
    int old = l->sel;
    l->sel = i < 0 ? 0 : i >= l->n ? l->n - 1 : i;
    moved(l, old);
    if (!animate) {
        l->cap_k = 1, l->cap_close = -1;
        settle(l);
        ui_mark(frame_rect(l));
    }
}

void list_event(ui_list *l, const input_t *e) {
    if (!l->n) return;
    if (ui_time() - live.input >= LIVE_IDLE_US) ui_again(); /* the cells at rest start again */
    live.input = ui_time();
    if (e->type == IN_TICK && l->grab >= 0) { /* a grabbed slider's: the selection stays */
        int step = wheel_steps(e->speed); /* by the wheel's speed, as demo's sliders (patch/levels.c wheel_level) */
        ui_row before = { 0 }, after = { 0 };
        get_row(l, l->grab, &before);
        if (l->src.adjust) l->src.adjust(l->src.ctx, l->grab, e->value * step); /* clockwise is more, as demo's sliders (patch/inline_slider.c) */
        get_row(l, l->grab, &after);
        if (after.level != before.level) ui_click(0); /* as demo's slider: only a change clicks */
        ui_mark(l->focus_was);
    } else if (e->type == IN_TICK) {
        int dir = e->value, step = wheel_accel(&l->accel, l, dir, e->ms, l->n), old = l->sel;
        int sel = step == WA_LETTER && l->src.letters ? letter_step(l, l->sel, dir)
                                                      : l->sel + dir * (step == WA_LETTER ? WA_MAX_ROWS : step);
        sel = sel < 0 ? 0 : sel >= l->n ? l->n - 1 : sel;
        while (sel > 0 && sel < l->n - 1 && !selectable(l, sel)) sel += dir;
        if (!selectable(l, sel)) sel = old;
        l->sb_at = ui_time();
        if (l->sb_alpha != 255) l->sb_alpha = 255, ui_mark(sb_rect());
        ui_wake_ms((SB_SHOW_US - SB_FADE_US) / 1000u);
        l->sel = sel;
        /* a tick throws the platter's lean the way it went, at a list's end too, where the
         * selection stays (docs/oled-design.md#selection-bar) */
        if (list_motion && !ui_reduce_motion) l->lean.v = (double)dir * KICK, kick(l);
        if (sel != old) moved(l, old), ui_click(0); /* at an end, where it stays, no click: as demo's lists */
        if (l->src.letters && l->n - l->src.pinned > AZ_ROWS && (step >= 2 || step == WA_LETTER) && sel >= l->src.pinned) {
            if (!l->az_in) l->az_in = AZ_IN, ui_mark(frame_rect(l)); /* the right column moves in for it */
            l->az_at = ui_time();
        }
        if (l->az_at) {
            struct rowc *c = row_text(l, l->sel);
            l->az_letter = az_of(c->row.title);
            ui_mark(az_rect());
        }
    } else if (e->type == IN_TUG) {
        if (!list_motion || ui_reduce_motion) return;
        float a = clampf(fabsf(e->share), 0, 1);
        l->tug = e->share;
        l->lean.to = (e->share < 0 ? -1.0f : 1.0f) * (1 - (1 - a) * (1 - a)) * LEAN_PX;
        kick(l);
    } else if (e->type == IN_KEY_DOWN && e->value == KEY_UI_CENTRE) {
        l->pressed = 1, l->press.to = -PRESS_SINK, l->lean.to = 0;
        if (!list_motion || ui_reduce_motion) spring_set(&l->press, l->press.to), ui_mark(l->focus_was);
        else kick(l);
    } else if ((e->type == IN_KEY_UP || e->type == IN_KEY_HOLD) && e->value == KEY_UI_CENTRE) {
        int was = l->pressed;
        l->pressed = 0, l->press.to = 0;
        if (!list_motion || ui_reduce_motion) spring_set(&l->press, 0), ui_mark(l->focus_was);
        else kick(l);
        if (e->type == IN_KEY_HOLD) {
            if (l->src.held) l->src.held(l->src.ctx, l->sel);
        } else if (was && row_text_flags(l, l->sel) & ROW_SLIDER) { /* Centre grabs a slider and sets it down */
            l->grab = l->grab == l->sel ? -1 : l->sel;
            ui_mark(l->focus_was);
        } else if (was && row_text_flags(l, l->sel) & ROW_PLAYING) { /* the song plays on: never sent again */
            ui_push(&page_now, 0);
        } else if (was && l->src.chosen)
            l->src.chosen(l->src.ctx, l->sel);
    }
}

static float slides_now(const ui_list *l) {
    float sum = 0;
    for (int k = 0; k < SLIDES; ++k)
        if (l->slides[k].at) sum += fabsf(slide_of(l, l->slides[k].row));
    return sum;
}
int list_last_selected(void) { return traced ? traced->sel : -1; }
/* The rows of the list last stepped (the page on top) into path, a line each: its title, then a tab
 * and its value when it has one, or with marks, a tab and its value, its detail and its marks
 * (spaced) each after a tab; a script's "rows FILE", for a test to read a page's text. */
int list_rows_dump(const char *path) {
    FILE *f = traced ? fopen(path, "w") : 0;
    if (!f) return -1;
    for (int i = 0; i < traced->n; ++i) {
        ui_row r = { 0 };
        get_row(traced, i, &r);
        if (r.marks[0]) {
            fprintf(f, "%s\t%s\t%s\t", r.title ? r.title : "", r.value ? r.value : "", r.detail ? r.detail : "");
            for (int k = 0; k < 3 && r.marks[k]; ++k) fprintf(f, "%s%s", k ? " " : "", r.marks[k]);
            fputc('\n', f);
        } else
            fprintf(f, "%s%s%s\n", r.title ? r.title : "", r.value ? "\t" : "", r.value ? r.value : "");
    }
    return fclose(f);
}
int list_layout_dump(const char *path) {
    FILE *f = traced ? fopen(path, "w") : 0;
    if (!f) return -1;
    float right = (float)(traced->box.x + traced->box.w - (SCREEN_W - RIGHT_COL) - traced->az_in);
    for (int i = traced->top; i < traced->n && i < traced->top + traced->rows + 1; ++i) {
        struct rowc *c = row_text(traced, i);
        ui_row r = { 0 };
        get_row(traced, i, &r); /* its length's words: a source's buffer holds only the row asked last */
        float dx = c->detail.n ? c->detail_x : 0, lx = c->length.n ? right - c->length.w : 0;
        fprintf(f, "%d\t%.1f %.1f\t%.1f %.1f\t%.1f %.1f\t%.1f\t%s\n", i, c->title_x, c->title_x + c->title.w, dx,
                c->detail.n ? dx + c->detail.w : 0, lx, c->length.n ? right : 0, c->row.flags & ROW_PLAYING ? c->bars_right : 0,
                c->length.n && r.length ? r.length : "");
    }
    return fclose(f);
}
int list_trace(char *out, size_t room) {
    if (!traced) return snprintf(out, room, "-");
    return snprintf(out, room, "sel=%d scroll=%.3f bar=%.3f lean=%.3f press=%.4f knob=%.3f grab=%d az=%d azin=%d cap=%.2f slide=%.2f",
                    traced->sel, traced->scroll.x, traced->bar.x, traced->lean.x, traced->press.x, traced->knob.x,
                    traced->grab, traced->az_at ? traced->az_letter : -1, traced->az_in, row_extra(traced, traced->sel), slides_now(traced));
}

void list_step(ui_list *l) {
    uint64_t now = ui_time();
    traced = l;
    if (l->src.song && playing_id() != l->playing_seen) /* another song plays: its row marked, the last's not */
        l->playing_seen = playing_id(), list_rows_changed();
    /* rows changed from outside (list_rows_changed): read again, the selected switch sliding over,
     * and counted again when rows came or went (a switch whose rows show only while it is on) */
    if (l->everything_seen != everything) {
        l->everything_seen = everything;
        if (l->src.count(l->src.ctx) != l->n) list_changed(l);
        list_reload(l);
    }
    float dt = now > l->t ? (float)(now - l->t) / 1e6f : 0;
    l->t = now;
    double scroll = l->scroll.x;
    int moving = 0;
    if (l->moving) {
        moving |= spring_step(&l->scroll, dt, GLIDE_S, 1);
        moving |= spring_step(&l->bar, dt, BAR_S, 1);
        moving |= spring_step(&l->lean, dt, LEAN_S, LEAN_DAMP);
        moving |= spring_step(&l->press, dt, l->pressed ? 0.09f : PRESS_S, l->pressed ? 1.0f : 0.55f);
        moving |= spring_step(&l->knob, dt, 0.2f, 1);
        l->moving = moving;
        if (moving) ui_again();
        if (l->scroll.x != scroll) ui_mark(frame_rect(l));
        rect_t f = focus_rect(l);
        ui_mark(rect_union(f, l->focus_was));
        l->focus_was = f;
    }
    float sliding = 0;
    for (int k = 0; k < SLIDES; ++k) { /* the rows a moved row passed, sliding aside */
        struct slide *sl = &l->slides[k];
        if (!sl->at) continue;
        if (now - sl->at >= SLIDE_US) sl->at = 0;
        sliding = 1;
    }
    if (sliding) ui_mark(frame_rect(l)), ui_again();
    if (l->cap_k < 1) { /* a caption's grow, eased out (EASE_QUAD_OUT, as demo's cap_step) */
        float t = clampf((float)(now - l->cap_at) / (float)CAP_US, 0, 1);
        l->cap_k = 1 - (1 - t) * (1 - t);
        if (t >= 1) l->cap_k = 1, l->cap_close = -1;
        else ui_again();
        rect_t f = focus_rect(l);
        ui_mark(frame_rect(l)), ui_mark(rect_union(f, l->focus_was));
        l->focus_was = f;
    }
    if (l->sb_at) {
        uint64_t t = now - l->sb_at;
        int a = t < SB_SHOW_US - SB_FADE_US ? 255 : t >= SB_SHOW_US ? 0 : (int)(255 * (SB_SHOW_US - t) / SB_FADE_US);
        if (t >= SB_SHOW_US)
            l->sb_at = 0;
        else if (t >= SB_SHOW_US - SB_FADE_US)
            ui_again();
        else
            ui_wake_ms((unsigned)((SB_SHOW_US - SB_FADE_US - t) / 1000u) + 1);
        if (a != l->sb_alpha) l->sb_alpha = a, ui_mark(sb_rect());
    }
    if (l->az_at) {
        uint64_t t = now - l->az_at;
        if (t >= AZ_SHOW_US)
            l->az_at = 0, l->az_in = 0, ui_mark(frame_rect(l)); /* gone: the right column back out */
        else if (t >= AZ_SHOW_US - AZ_FADE_US)
            ui_mark(az_rect()), ui_again();
        else {
            if (t >= AZ_BUBBLE_US) ui_mark(az_rect()); /* the bubble gone */
            ui_wake_ms((unsigned)(((t < AZ_BUBBLE_US ? AZ_BUBBLE_US : AZ_SHOW_US - AZ_FADE_US) - t) / 1000u) + 1);
        }
    }
    if (l->n && !l->lifted && list_living && list_motion && live_takes(RGB_ACCENT)) { /* the living row's clock */
        uint64_t idle = now - live.input;
        float rate = idle < LIVE_IDLE_US ? 1 : idle >= LIVE_IDLE_US + LIVE_EASE_US ? 0 : 1 - (float)(idle - LIVE_IDLE_US) / LIVE_EASE_US;
        rate = 1 - (1 - rate) * (1 - rate); /* eased out: slowing most at its end */
        if (ui_reduce_motion || ui_dark()) rate = 0;
        if (rate > 0 && live.t) {
            live.clock += (double)(now - live.t) * rate;
            ui_mark(rect_of(platter(l, 0, 0), 0)), ui_wake_ms(1000u / (unsigned)(list_living_fps > 0 ? list_living_fps : 60));
        } else if (rate > 0)
            ui_wake_ms(1000u / (unsigned)(list_living_fps > 0 ? list_living_fps : 60));
        live.t = rate > 0 ? now : 0;
    }
    if (l->mq_at) {
        /* the selected row's cut title: rest, slide to its end, rest, back to the start */
        struct rowc *c = row_text(l, l->sel);
        uint64_t t = now - l->mq_at;
        if (l->mq_row != l->sel) {
            run_make(&l->mq_run, FACE_REGULAR, TITLE_PX, c->row.title ? c->row.title : "", -1);
            l->mq_row = l->sel;
        }
        float over = l->mq_run.w - (c->text_end - c->title_x), x = 0;
        uint64_t slide = over > 0 ? (uint64_t)(over / MQ_SPEED * 1e6f) : 0;
        if (over <= 0)
            l->mq_at = 0;
        else if (t < MQ_REST_US)
            ui_wake_ms((unsigned)((MQ_REST_US - t) / 1000u) + 1);
        else if (t < MQ_REST_US + slide)
            x = (float)(t - MQ_REST_US) / 1e6f * MQ_SPEED, ui_again();
        else if (t < MQ_REST_US + slide + MQ_END_US)
            x = over, ui_wake_ms((unsigned)((MQ_REST_US + slide + MQ_END_US - t) / 1000u) + 1);
        else
            l->mq_at = now, ui_wake_ms(MQ_REST_US / 1000u);
        if (x != l->mq_x) {
            l->mq_x = x;
            ui_mark(l->focus_was);
        }
    }
}

/* ---- a page that is one list ---- */
struct list_page_st {
    ui_list *list;
    char name[48];
};
static void *lp_open(void *arg) {
    struct list_page_st *s = calloc(1, sizeof *s);
    const ui_source *src = arg;
    s->list = list_new(src);
    if (src->name) snprintf(s->name, sizeof s->name, "%s", src->name);
    return s;
}
static int lp_where(void *st, char *name, size_t room) {
    struct list_page_st *s = st;
    snprintf(name, room, "%s", s->name);
    return s->list->sel;
}
static void lp_select(void *st, int sel) { list_select(((struct list_page_st *)st)->list, sel, 0); }
static void lp_event(void *st, const input_t *e) {
    struct list_page_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        if (!list_set_down(s->list)) ui_pop(); /* Return sets a grabbed slider down first */
        return;
    }
    list_event(s->list, e);
}
static void lp_step(void *st) { list_step(((struct list_page_st *)st)->list); }
static void lp_draw(void *st, rect_t r) {
    list_draw(((struct list_page_st *)st)->list, r);
}
static void lp_close(void *st) {
    struct list_page_st *s = st;
    list_free(s->list);
    free(s);
}
static void lp_shown(void *st) {
    (void)st;
    ui_mark_all();
}
const ui_page list_page = { "list", lp_open, lp_event, lp_step, lp_draw, lp_close, lp_shown, lp_where, lp_select };
ui_list *list_page_list(void *st) { return ((struct list_page_st *)st)->list; }
