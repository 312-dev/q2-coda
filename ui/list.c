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
 *  - the press: Centre sinks the platter and brightens it, the release springs it back.
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

#include "../patch/wheel_accel.h"
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
#define SLIDER_STEPS 10    /* a tick's most steps on a fast spin (patch/offsets.inc VOL_ACCEL_MAX) */
#define SLIDER_TRACK_ON 0x7da2ea /* its track on the platter, under a white fill */
#define CACHE 32 /* rows whose text is kept: a power of two, more than a fast glide shows */

int list_motion = 1;

struct rowc {
    int index; /* -1: empty */
    unsigned pictures, everything; /* list_pictures and list_rows_changed's count when the row was asked for */
    ui_row row;
    run_t title, detail, value, sub, lead;
    float title_x, detail_x, text_end; /* where the title's column ends */
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
};

static int selectable(ui_list *l, int i) {
    ui_row r = { 0 };
    l->src.row(l->src.ctx, i, &r);
    return !(r.flags & (ROW_GREY | ROW_HEADER));
}
static float row_y(const ui_list *l, int i) {
    if (i < l->src.pinned) return (float)(l->box.y + i * l->row_h);
    return (float)((double)(l->y0 + (i - l->src.pinned) * l->row_h) - l->scroll.x);
}
static float bar_to(const ui_list *l) {
    if (l->sel < l->src.pinned) return (float)(l->box.y + l->sel * l->row_h);
    return (float)(l->y0 + (l->sel - l->top) * l->row_h);
}
static rect_t frame_rect(const ui_list *l) {
    /* a list under the status row runs on under it to the screen's top and down to its foot */
    if (l->box.y == LIST_TOP) return (rect_t){ l->box.x, 0, l->box.w, SCREEN_H };
    return l->box;
}
static rect_t sb_rect(void) { return (rect_t){ SB_X, SB_TOP, SB_W, SB_BOTTOM - SB_TOP }; }

/* The platter now: its row's box grown by the raise, the lean and the press, moved by the lean. */
static rectf_t platter(const ui_list *l, float *tilt, float *lean_share) {
    float share = clampf((float)l->lean.x / LEAN_PX, -1.5f, 1.5f);
    float scale = 1 + RAISE + LEAN_GROW * fabsf(share) + (float)l->press.x;
    float w = (float)(l->box.w - 2 * SEL_INSET) * scale, h = (float)l->row_h * scale;
    float cx = (float)l->box.x + (float)l->box.w / 2;
    float cy = (float)l->bar.x + (float)l->row_h / 2 + share * LEAN_PX;
    if (tilt) *tilt = -share * LEAN_TILT;
    if (lean_share) *lean_share = share;
    return (rectf_t){ cx - w / 2, cy - h / 2, w, h };
}
/* everything the focus layer can touch: the platter and its shadow */
static ui_list *traced;
static rect_t focus_rect(const ui_list *l) { return rect_of(platter(l, 0, 0), 10); }

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
    if (l->sel >= p) {
        if (l->sel < l->top) l->top = l->sel;
        if (l->sel >= l->top + l->rows) l->top = l->sel - l->rows + 1;
    }
    int last = l->n - l->rows;
    if (l->top > last) l->top = last > p ? last : p;
    l->scroll.to = (double)((l->top - p) * l->row_h);
    l->bar.to = bar_to(l);
}

static unsigned row_text_flags(ui_list *l, int i) {
    ui_row r = { 0 };
    l->src.row(l->src.ctx, i, &r);
    return r.flags;
}
static void forget(ui_list *l) {
    for (int i = 0; i < CACHE; ++i) l->rows_c[i].index = -1;
    l->mq_row = -1;
}

/* Bumped by list_pictures_changed and list_rows_changed (below) */
static unsigned pictures, everything;

ui_list *list_new(const ui_source *src) {
    ui_list *l = calloc(1, sizeof *l);
    l->src = *src;
    l->box = src->box.w ? src->box : (rect_t){ 0, LIST_TOP, SCREEN_W, LIST_END - LIST_TOP };
    l->row_h = src->row_h ? src->row_h : ROW_H;
    l->y0 = l->box.y + src->pinned * l->row_h;
    l->rows = (l->box.y + l->box.h - l->y0) / l->row_h;
    l->n = src->count(src->ctx);
    l->grab = -1;
    forget(l);
    /* a list opens on its first row that scrolls: a pinned row (Shuffle) is reached by the wheel */
    l->sel = src->pinned < l->n ? src->pinned : 0;
    while (l->sel < l->n - 1 && !selectable(l, l->sel)) ++l->sel;
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

/* Rows changed in place (a switch flipped, a value cycled): the same rows are read again, and the
 * selected row's switch slides to its new side. */
void list_reload(ui_list *l) {
    int on = 0;
    forget(l);
    if (l->n) on = row_text_flags(l, l->sel) & ROW_ON;
    l->knob.to = on ? 1 : 0;
    if (!list_motion) spring_set(&l->knob, l->knob.to);
    else kick(l);
    ui_mark(frame_rect(l));
}

void list_changed(ui_list *l) {
    l->n = l->src.count(l->src.ctx);
    forget(l);
    if (l->sel >= l->n) l->sel = l->n ? l->n - 1 : 0;
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
        !run_stale(&c->sub) && !run_stale(&c->lead) && (!c->row.art || c->pictures == pictures) && c->everything == everything)
        return c;
    static run_t gap;
    if (!gap.n || run_stale(&gap)) run_make(&gap, FACE_REGULAR, TITLE_PX, "  ", -1);
    ui_row *r = &c->row;
    memset(r, 0, sizeof *r);
    l->src.row(l->src.ctx, i, r);
    c->index = i, c->pictures = pictures, c->everything = everything;
    int two = l->row_h > ROW_H && r->sub;
    float x = (float)(l->box.x + TEXT_X + TEXT_MARGIN);
    if (r->icon) x = (float)(l->box.x + ICON_TEXT_X + TEXT_MARGIN);
    if (r->lead) x = (float)(l->box.x + LEAD_TEXT_X + TEXT_MARGIN);
    if (r->art) x = (float)(l->box.x + ART_TEXT_X + TEXT_MARGIN);
    float end = (float)(l->box.x + l->box.w - (SCREEN_W - RIGHT_COL)) - TEXT_MARGIN;
    c->value.n = c->detail.n = c->sub.n = c->lead.n = 0;
    c->value.made = c->detail.made = c->sub.made = c->lead.made = c->title.made;
    if (r->flags & ROW_HEADER) {
        run_make(&c->title, FACE_REGULAR, META_PX, r->title ? r->title : "", (int)(end - x));
        c->title_x = x, c->text_end = end;
        c->value.made = c->detail.made = c->sub.made = c->lead.made = c->title.made;
        return c;
    }
    if (r->flags & ROW_SLIDER) { /* the title before the track; the value after it, in small text */
        if (r->value) run_make(&c->value, FACE_REGULAR, META_PX, r->value, SLIDER_END - 8);
        end = (float)(l->box.x + SLIDER_X) - VALUE_GAP;
    } else if (r->value && *r->value) {
        run_make(&c->value, FACE_REGULAR, two ? META_PX : TITLE_PX, r->value, (int)((end - x) / 2));
        end -= c->value.w + VALUE_GAP;
    } else if (r->flags & ROW_SWITCH)
        end -= 40 + VALUE_GAP;
    else if (r->flags & (ROW_CHEVRON | ROW_CHECK | ROW_PLAYING))
        end -= 20;
    run_make(&c->title, FACE_REGULAR, TITLE_PX, r->title ? r->title : "", (int)(end - x));
    c->title_x = x, c->text_end = end;
    float left = end - x - c->title.w - gap.w;
    if (!two && r->detail && *r->detail && left >= DETAIL_MIN) {
        run_make(&c->detail, FACE_REGULAR, META_PX, r->detail, (int)left);
        c->detail_x = x + c->title.w + gap.w;
    }
    if (two) run_make(&c->sub, FACE_REGULAR, META_PX, r->sub, (int)(end - x));
    if (r->lead) run_make(&c->lead, FACE_REGULAR, META_PX, r->lead, -1);
    c->value.made = c->detail.made = c->sub.made = c->lead.made = c->title.made;
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
    int grey = r->flags & ROW_GREY, accent = r->flags & (ROW_ACCENT | ROW_PLAYING);
    uint32_t ink = focused ? on : grey ? tok(TOK_TERTIARY) : accent ? RGB_ACCENT : tok(TOK_PRIMARY);
    uint32_t dim = focused ? on : tok(TOK_TERTIARY);
    uint32_t val = focused ? on : grey ? tok(TOK_TERTIARY) : tok(TOK_SECONDARY);
    if (r->icon)
        image_draw(r->icon, (rectf_t){ (float)(l->box.x + TEXT_X), cy - ICON_PX / 2 + iy, ICON_PX, ICON_PX }, 0, 0,
                   256, focused ? (long)on : accent ? (long)RGB_ACCENT : (long)tok(TOK_SECONDARY));
    if (r->art)
        image_draw(r->art, (rectf_t){ (float)(l->box.x + TEXT_X), y + 6 + iy, ART_PX, ART_PX }, 4, 0, 256, -1);
    if (c->lead.n) run_draw(&c->lead, (float)(l->box.x + LEAD_END) - c->lead.w, ty + (h - (float)META_PX) / 2 + META_DY, dim, 256);
    int two = c->sub.n > 0;
    float title_y = two ? ty + 5 : ty + (h - (float)TITLE_PX) / 2;
    if (focused && i == l->mq_row && l->mq_run.n) {
        /* the cut title, whole, sliding in its column with its ends faded */
        fade_set((int)c->title_x, (int)c->text_end, l->mq_x > 0.5f ? MQ_FADE : 0, MQ_FADE);
        run_draw(&l->mq_run, c->title_x - l->mq_x, title_y, ink, 256);
        fade_clear();
    } else
        run_draw(&c->title, c->title_x, title_y, ink, 256);
    if (c->detail.n) run_draw(&c->detail, c->detail_x, ty + (h - (float)META_PX) / 2 + META_DY, dim, 256);
    if (two) run_draw(&c->sub, c->title_x, ty + 29, dim, 256);
    float right = (float)(l->box.x + l->box.w - (SCREEN_W - RIGHT_COL));
    if (r->flags & ROW_SLIDER) {
        slider((float)(l->box.x + SLIDER_X), right - SLIDER_END, cy + iy, r->level, focused, i == l->grab);
        if (c->value.n) run_draw(&c->value, right - c->value.w, ty + (h - (float)META_PX) / 2, val, 256);
    } else if (c->value.n)
        run_draw(&c->value, right - c->value.w, two ? ty + 5 + 3 : ty + (h - (float)TITLE_PX) / 2, val, 256);
    else if (r->flags & ROW_SWITCH)
        toggle(right, cy + iy, i == l->sel ? (float)l->knob.x : r->flags & ROW_ON ? 1.0f : 0.0f, focused);
    else if (r->flags & ROW_PLAYING)
        level_bars(right, cy + iy, focused ? on : RGB_ACCENT);
    else if (r->flags & ROW_CHECK)
        check(right - 7, cy + iy, focused ? on : RGB_ACCENT);
    else if (r->flags & ROW_CHEVRON)
        chevron(right - 5, cy + iy, focused ? on : grey ? tok(TOK_TERTIARY) : tok(TOK_CHEVRON));
}

static void draw_indicator(const ui_list *l) {
    int total = l->n - l->src.pinned;
    if (!l->sb_alpha || total <= l->rows) return;
    float track = SB_BOTTOM - SB_TOP, len = track * (float)l->rows / (float)total;
    if (len < SB_MIN) len = SB_MIN;
    float at = (track - len) * clampf((float)(l->scroll.x / (double)((total - l->rows) * l->row_h)), 0, 1);
    float a = (float)l->sb_alpha / 255;
    fill_round((rectf_t){ SB_X, SB_TOP, SB_W, track }, 2, 0, 0x3a3a3a, a, 0);
    fill_round((rectf_t){ SB_X, SB_TOP + at, SB_W, len }, 2, 0, 0xe5e5e5, a, 0);
}

void list_draw(ui_list *l, rect_t r) {
    rect_t frame = frame_rect(l), part, rows;
    int p = l->src.pinned, full = l->box.y == LIST_TOP;
    if (!rect_meet(r, frame, &part)) return;
    clip_set(part);
    fill(part, tok(TOK_CANVAS));
    /* the scrolling rows, under everything: clipped to the list's frame, which under a pinned
     * row starts at that row's foot */
    rows = frame;
    if (p) rows.h -= l->y0 - rows.y, rows.y = l->y0;
    if (rect_meet(part, rows, &rows) && l->n > p) {
        clip_set(rows);
        int first = p + (int)floor((l->scroll.x + (double)(rows.y - l->y0)) / (double)l->row_h);
        if (first < p) first = p;
        for (int i = first; i < l->n; ++i) {
            float y = row_y(l, i);
            if (y >= (float)(rows.y + rows.h)) break;
            row_paint(l, i, y, 0, 0);
        }
        /* the list's ends: rows fade into the screen's foot, and into its top once scrolled */
        if (full || p) {
            int foot = l->box.y + l->box.h;
            fade_rows(foot, FOOT_END, FOOT_KEEP, 0);
            fill((rect_t){ frame.x, FOOT_END, frame.w, SCREEN_H - FOOT_END }, tok(TOK_CANVAS));
        }
        if (p)
            fade_rows(l->y0, l->y0 + PIN_FADE, 0, 255);
        else if (full && l->scroll.x > 0) {
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
    float pressed = clampf((float)-l->press.x / PRESS_SINK, 0, 1);
    uint32_t blue = rgb_mix(RGB_ACCENT, RGB_ACCENT_PRESSED, pressed);
    shadow_round((rectf_t){ bar.x + 2, bar.y + 3, bar.w - 4, bar.h }, SEL_R, 7, RGB_ACCENT,
                 (0.30f + 0.25f * fabsf(share)) * (1 - pressed));
    gleam_t gleam = { 0, -share * (float)l->row_h * 0.6f, bar.w * 0.45f, (float)l->row_h * 1.1f,
                      0.42f * clampf((fabsf(share) - 0.15f) / 0.85f, 0, 1) };
    fill_round(bar, SEL_R, tilt, blue, 1, &gleam);
    /* what the platter covers reads in its focused inks, cut to the platter */
    if (!rect_meet(r, rect_of(bar, 0), &on)) return;
    clip_set(on);
    int near = p + (int)floor(((double)(bar.y - (float)l->y0) + l->scroll.x) / (double)l->row_h);
    for (int k = 0; k < p + 4; ++k) {
        int i = k < p ? k : near + k - p - 1;
        if (i < (k < p ? 0 : p) || i >= l->n) continue;
        float y = row_y(l, i);
        if (y >= bar.y + bar.h || y + (float)l->row_h <= bar.y) continue;
        /* how much of the row the platter is over: a row it is leaving moves less with it */
        float over = clampf(1 - fabsf(y - (float)l->bar.x) / (float)l->row_h, 0, 1);
        row_paint(l, i, y, 1, share * LEAN_PX * over);
    }
    clip_set(r);
}

/* The first row at or after i, going dir, whose title starts with another letter than row i's */
static int letter_step(ui_list *l, int i, int dir) {
    ui_row r = { 0 };
    int p = l->src.pinned;
    if (i < p) return dir > 0 ? p : i;
    l->src.row(l->src.ctx, i, &r);
    int c = (unsigned char)(r.title ? r.title[0] : 0) | 0x20, j = i;
    for (;;) {
        if (j + dir < p || j + dir >= l->n) return j;
        l->src.row(l->src.ctx, j + dir, &r);
        j += dir;
        if (((unsigned char)(r.title ? r.title[0] : 0) | 0x20) != c) return j;
    }
}

static void moved(ui_list *l, int old) {
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
        settle(l);
        ui_mark(frame_rect(l));
    }
}

void list_event(ui_list *l, const input_t *e) {
    if (!l->n) return;
    if (e->type == IN_TICK && l->grab >= 0) { /* a grabbed slider's: the selection stays */
        int step = wheel_accel(&l->accel, l, e->value, e->ms, 0);
        step = step < 1 ? 1 : step > SLIDER_STEPS ? SLIDER_STEPS : step;
        if (l->src.adjust) l->src.adjust(l->src.ctx, l->grab, -e->value * step); /* down the wheel is less */
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
        if (sel != old) moved(l, old);
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
        } else if (was && l->src.chosen)
            l->src.chosen(l->src.ctx, l->sel);
    }
}

int list_last_selected(void) { return traced ? traced->sel : -1; }
/* The rows of the list last stepped (the page on top) into path, a line each: its title, then a tab
 * and its value when it has one; a script's "rows FILE", for a test to read a page's text. */
int list_rows_dump(const char *path) {
    FILE *f = traced ? fopen(path, "w") : 0;
    if (!f) return -1;
    for (int i = 0; i < traced->n; ++i) {
        ui_row r = { 0 };
        traced->src.row(traced->src.ctx, i, &r);
        fprintf(f, "%s%s%s\n", r.title ? r.title : "", r.value ? "\t" : "", r.value ? r.value : "");
    }
    return fclose(f);
}
int list_trace(char *out, size_t room) {
    if (!traced) return snprintf(out, room, "-");
    return snprintf(out, room, "sel=%d scroll=%.3f bar=%.3f lean=%.3f press=%.4f knob=%.3f grab=%d", traced->sel,
                    traced->scroll.x, traced->bar.x, traced->lean.x, traced->press.x, traced->knob.x, traced->grab);
}

void list_step(ui_list *l) {
    uint64_t now = ui_time();
    traced = l;
    /* rows changed from outside (list_rows_changed): read again, the selected switch sliding over */
    if (l->everything_seen != everything) l->everything_seen = everything, list_reload(l);
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
