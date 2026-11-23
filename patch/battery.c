/* Battery History: the level's falls since the charger was last unplugged, kept on the card, and
 * Power's page that graphs them, reads one out under the wheel and shares them over LocalSend. */
#include "navigation.h"
#include "card_store.h"

/* Battery History (docs/internals.md#battery-history): charge_poll hands battery_tick the level it
 * reads every CHARGE_POLL_MS. With the cable out, a level under the last row's is a row, the time
 * and the level; the first row is the start: the first poll after the cable comes out, or after a
 * boot with none in. A level over the last row's by more than BH_REBOUND starts again too: the Q2
 * charged while powered off, where demo saw no cable. Each row is one write of the record as far
 * as its rows go (BH_LEN: 8 bytes a row after an 8-byte head), temp and rename, to the card
 * (card_store.c: kept in RAM while the card is away and written when it is back; the flash file
 * earlier releases wrote is read until then, and copied once), so nothing here writes the flash.
 * Within a history the level only falls, from 100 at most to 1 (0 never reads: demo powers off
 * first), so BH_ROWS rows always hold one. ponytail: a charge of BH_REBOUND or less while powered
 * off reads as the same history, its rows below the last one's level again. */
#define BH_FLASH PEQ_ROOT "/mnt/data/q2-battery" /* before Q2 Muse kept it on the card */
#define BH_FILE CARD_STORE_DIR "/q2-battery"
#define BH_MAGIC 0x31484251u /* "QBH1" */
#define BH_ROWS 100
#define BH_REBOUND 3
static struct {
    struct {
        unsigned magic, n;
        struct {
            unsigned at, pct; /* wall_time's seconds, the level */
        } row[BH_ROWS];
    } rec;
    int read, cable; /* cable: in at the last tick */
    int cur;         /* the reading the wheel is on, its row; -1 the newest, following new ones */
    void *page, *graph;
    rect_t button; /* Share Log's, on screen, as last painted (0 wide: not drawn) */
    card_blob store;
} bh __attribute__((section(".scratch")));
#define BH_STORE card_blob_at(&bh.store, BH_FILE, BH_FLASH, sizeof bh.rec)
/* the record's bytes with n rows: its head, then the rows; a file written whole (808 bytes, as
 * releases before wrote it) reads as well */
#define BH_LEN(n) ((unsigned)((char *)&bh.rec.row[n] - (char *)&bh.rec))

/* The record from the card, once; one shorter than its rows, not this layout or whose levels don't
 * fall row by row is dropped, and the next tick starts again. */
static void bh_load(void) {
    if (bh.read) return;
    bh.read = 1;
    int len = card_blob_read(BH_STORE, &bh.rec, sizeof bh.rec);
    int ok = len >= (int)BH_LEN(0) && bh.rec.magic == BH_MAGIC && bh.rec.n <= BH_ROWS &&
             len >= (int)BH_LEN(bh.rec.n);
    for (unsigned i = 0; ok && i < bh.rec.n; ++i)
        ok = bh.rec.row[i].pct &&
             (i ? bh.rec.row[i].pct < bh.rec.row[i - 1].pct : bh.rec.row[i].pct <= 100);
    if (!ok) memset(&bh.rec, 0, sizeof bh.rec);
}

void battery_tick(int level, int cable) {
    if (level <= 0 || level > 100) return; /* not read yet */
    bh_load();
    card_blob_flush(BH_STORE); /* a row kept in RAM while the card was away */
    unsigned n = bh.rec.n;
    int unplugged = bh.cable && !cable;
    bh.cable = cable;
    if (cable) return;
    if (unplugged || !n || level > (int)bh.rec.row[n - 1].pct + BH_REBOUND)
        n = 0;
    else if (level >= (int)bh.rec.row[n - 1].pct)
        return;
    long long wall = wall_time();
    bh.rec.magic = BH_MAGIC;
    bh.rec.row[n].at = wall == -1 ? 0 : (unsigned)wall;
    bh.rec.row[n].pct = (unsigned)level;
    bh.rec.n = n + 1;
    card_blob_write(BH_STORE, &bh.rec, BH_LEN(n + 1));
    if (bh.page) widget_invalidate_force(bh.page, (void *)0); /* drawn at the next lit frame */
}

/* Row i's local time, or 0 for a clock never set. */
static const int *bh_tm(unsigned i, int *buf) {
    long t = clock_back(bh.rec.row[i].at);
    return bh.rec.row[i].at >= CLOCK_SET ? localtime_r(&t, buf) : (void *)0;
}
/* Row i as "99% | 12:00": the clock's time, 12-hour as the status bar's unless 24-hour is on, or
 * "+1:23", the hours and minutes since the start, for a clock never set. */
static void bh_text(char *s, unsigned size, unsigned i) {
    int buf[11];
    const int *tm = bh_tm(i, buf);
    unsigned at = bh.rec.row[i].at, from = bh.rec.row[0].at, m = (at - from) / 60,
             pct = bh.rec.row[i].pct;
    if (tm && g_time24h_flag)
        tk_snprintf(s, size, "%u%% | %02d:%02d", pct, tm[2], tm[1]);
    else if (tm)
        tk_snprintf(s, size, "%u%% | %d:%02d %s", pct, (tm[2] + 11) % 12 + 1, tm[1],
                    tm[2] < 12 ? "AM" : "PM");
    else if (at >= from)
        tk_snprintf(s, size, "%u%% | +%u:%02u", pct, m / 60, m % 60);
    else /* the clock lost after the start */
        tk_snprintf(s, size, "%u%% | --:--", pct);
}
/* Row i's date, "11 Nov 2026" (year) or "11 Nov", when it is the first row or a day after the row
 * before's; else "". */
static void bh_day(char *s, unsigned size, unsigned i, int year) {
    static const char months[12][4] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    int buf[11], was[11];
    const int *tm = bh_tm(i, buf), *before = i ? bh_tm(i - 1, was) : (void *)0;
    *s = 0;
    if (!tm || (unsigned)tm[4] > 11 ||
        (before && before[3] == tm[3] && before[4] == tm[4] && before[5] == tm[5]))
        return;
    if (year)
        tk_snprintf(s, size, "%d %s %d", tm[3], months[tm[4]], tm[5] + 1900);
    else
        tk_snprintf(s, size, "%d %s", tm[3], months[tm[4]]);
}

/* The log's text into out: each day's date over its rows, or, for a clock never set, a line that
 * says the times count from the start. Returns its length. */
unsigned battery_log(char *out, unsigned size) {
    unsigned len = 0;
    *out = 0;
    if (bh.rec.n && bh.rec.row[0].at < CLOCK_SET)
        tk_snprintf(out, size, "Clock not set: times since the start\n");
    for (unsigned i = 0; i < bh.rec.n && (len = strlen(out)) < size; ++i) {
        char day[32], row[48];
        bh_day(day, sizeof day, i, 1);
        bh_text(row, sizeof row, i);
        tk_snprintf(out + len, size - len, *day ? "%s%s\n%s\n" : "%s%s%s\n",
                    len && *day ? "\n" : "", day, row);
    }
    return strlen(out);
}

/* The log written whole to path (temp and rename): its bytes, or -1. LocalSend sends it (localsend_ui.c). */
int battery_log_write(const char *path) {
    char out[BH_ROWS * 64 + 64], tmp[64];
    bh_load();
    unsigned n = battery_log(out, sizeof out);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!blob_io(path, tmp, out, n, 1)) return unlink(tmp), -1;
    return (int)n;
}

/* Share Log: the log in /tmp, then LocalSend's share sheet with it (docs/localsend.md#sending). */
static void bh_share(void) {
    if (battery_log_write(PEQ_ROOT "/tmp/battery-log.txt") < 0)
        toast("Couldn't write the log");
    else
        localsend_share(PEQ_ROOT "/tmp/battery-log.txt", "Share Log");
}

/* The page (33 v3, laid out again as the user's mock-up of 2026-11-18, docs/oled-design.md#battery-history):
 * under the status row the reading the wheel is on, "Nov 16, 03:10 · 51%", then the graph, 100% to
 * 0% from BH_TOP to BH_BOT on screen, then Share Log, the shared grey button, its foot on the list
 * end (y 282) clear of the glass's corners. The wheel moves along the readings (Centre and a tap on
 * the button share), so the button is the page's only action and never wears the focus ring.
 * The toasts show in the button's place: it gives way while one is up. */
#define BH_READ_CY 66
#define BH_TOP 84
#define BH_BOT 226
#define BH_LABEL_W 48 /* the axis labels' column, right-aligned, from page_x */
#define BH_GAP 8      /* between the labels and the plot */
#define BH_DOT 10     /* the reading's dot, white */
#define BH_BTN_CY 263 /* y 244 to 282 */

/* The reading the wheel is on. */
static unsigned bh_cur(void) {
    return bh.cur >= 0 && (unsigned)bh.cur < bh.rec.n ? (unsigned)bh.cur : bh.rec.n - 1;
}
/* Row i as the readout's "Nov 16, 3:10 PM · 51%" (12-hour as the status bar's unless 24-hour is
 * on), "+1:23 · 51%" for a clock never set (the time since the start), "51%" for one lost since. */
static void bh_readout(char *s, unsigned size, unsigned i) {
    static const char months[12][4] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    int buf[11];
    const int *tm = bh_tm(i, buf);
    unsigned at = bh.rec.row[i].at, from = bh.rec.row[0].at, m = (at - from) / 60,
             pct = bh.rec.row[i].pct;
    if (tm && (unsigned)tm[4] <= 11 && g_time24h_flag)
        tk_snprintf(s, size, "%s %d, %02d:%02d \xc2\xb7 %u%%", months[tm[4]], tm[3], tm[2], tm[1], pct);
    else if (tm && (unsigned)tm[4] <= 11)
        tk_snprintf(s, size, "%s %d, %d:%02d %s \xc2\xb7 %u%%", months[tm[4]], tm[3],
                    (tm[2] + 11) % 12 + 1, tm[1], tm[2] < 12 ? "AM" : "PM", pct);
    else if (!tm && at >= from)
        tk_snprintf(s, size, "+%u:%02u \xc2\xb7 %u%%", m / 60, m % 60, pct);
    else
        tk_snprintf(s, size, "%u%%", pct);
}

static int bh_closed(void *ctx, void *event) {
    bh.page = bh.graph = 0;
    bh.button.w = 0;
    return 0;
}
/* ringnav, before the lists: the wheel along the readings (clockwise to the newer), Centre shares the log. */
int battery_key(void *top, unsigned key) {
    if (!bh.page || top != bh.page || !bh.rec.n) return 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        int at = clamp_step((int)bh_cur(), (int)bh.rec.n - 1, key == KEY_NEXT ? 1 : -1);
        bh.cur = at == (int)bh.rec.n - 1 ? -1 : at;
        widget_invalidate_force(bh.page, (void *)0);
        return 1;
    }
    if (key != KEY_CENTER) return 0;
    bh_share();
    return 1;
}
/* A tap on the button shares the log. */
static int bh_touch(void *ctx, void *event) {
    int x = I(event, EVENT_X), y = I(event, EVENT_Y);
    rect_t *b = &bh.button;
    if (b->w && x >= b->x && x < b->x + b->w && y >= b->y && y < b->y + b->h) bh_share();
    return 0;
}
/* Power's Battery History row. No history: "Unplug to start". Else the status row's title (a row
 * of the page's list, which stays hidden), and the graph's view over the rest. */
int battery_history(void *ctx, void *event) {
    if (bh.page || !(bh.page = page_open("battery_page", bh_closed, page_keyup))) return 0;
    bh_load();
    bh.cur = -1;
    unsigned n = bh.rec.n;
    void *view = page_list_or(bh.page, bh.page, "Battery History", "Unplug to start", n ? 1 : 0, 48);
    if (!n) return 0;
    void *list = P(view, W_PARENT);
    int y = I(list, W_Y), h = widget_get_prop_int(bh.page, "h", client_h());
    widget_set_visible(list, 0, 0);
    bh.graph = widget_factory_create_widget(widget_factory(), "view", bh.page, 0, y, 375, h - y);
    widget_on(bh.page, EVT_POINTER_UP_BEFORE, bh_touch, 0);
    return 0;
}

/* Row i's x in the plot, from x0 to x0 + w: by its time from the start up to now (to the last row's
 * when now reads earlier, or on the other side of CLOCK_SET from the rows: the clock set or lost
 * since), or evenly by row when the times don't run forward on one clock (set or lost
 * mid-history: forward = 0, from battery_paint). 32-bit: the payload has no 64-bit division. */
static int bh_x(unsigned i, int x0, int w, unsigned now, int forward) {
    unsigned n = bh.rec.n, from = bh.rec.row[0].at, end = bh.rec.row[n - 1].at;
    int set = from >= CLOCK_SET;
    if (!forward) return x0 + (int)i * w / (int)n;
    if ((now >= CLOCK_SET) != set || now < end) now = end;
    unsigned span = now - from, at = i < n ? bh.rec.row[i].at - from : span;
    while (span > 0xffff) span >>= 1, at >>= 1;
    return x0 + (span ? (int)(at * (unsigned)w / span) : (int)i * w / (int)n);
}
/* The page (ringnav_paint, on the graph's view): the readout, faint lines at 100% and 50% and the
 * axis at 0%, labelled in 15 px Text Tertiary, the level as steps from the start to now, a 2 px
 * line in the accent's light tone over a faint fill, as peq_ui.c's curve, the reading's line and
 * dot, and Share Log unless a toast is up. Positions are the screen's, less the canvas's origin
 * on the screen: the view's y counts from its window, and the view rests at y 58, the status row's foot. */
void battery_paint(void *w, void *canvas) {
    if (!w || w != bh.graph || !P(canvas, CANVAS_LCD) || !bh.rec.n) return;
    static const char *const labels[] = { "100%", "50%", "0%" };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR),
             tone = accent_tone(TONE_LIGHT), raw = st.raw_ink, cur = bh_cur();
    long long wall = wall_time();
    int dy = I(canvas, CANVAS_Y), x0 = page_x() + BH_LABEL_W + BH_GAP, plot = 375 - page_x() - x0,
        top = BH_TOP - dy, h = BH_BOT - BH_TOP;
    char s[64];
    st.raw_ink = 1;
    bh_readout(s, sizeof s, cur);
    ui_text(canvas, s, 1, TYPE(17), tok(TOK_PRIMARY), 1, 375 / 2, BH_READ_CY - dy, 375 - 48, 1);
    for (int k = 0; k < 3; ++k) {
        int y = top + k * h / 2;
        canvas_set_fill_color(canvas, rgba(k == 2 ? 0x4a4a4a : 0x262626, 255));
        canvas_fill_rect(canvas, x0, y, plot, 1);
        ui_text(canvas, labels[k], 0, TYPE(15), tok(TOK_TERTIARY), 1, page_x() + BH_LABEL_W, y,
                BH_LABEL_W, 3);
    }
    unsigned now = wall == -1 ? 0 : (unsigned)wall;
    int set = bh.rec.row[0].at >= CLOCK_SET, forward = 1, cx = x0, cy = top;
    for (unsigned k = 1; k < bh.rec.n; ++k)
        forward &=
            bh.rec.row[k].at >= bh.rec.row[k - 1].at && (bh.rec.row[k].at >= CLOCK_SET) == set;
    int x = bh_x(0, x0, plot, now, forward);
    for (unsigned i = 0; i < bh.rec.n; ++i) {
        int next = bh_x(i + 1, x0, plot, now, forward),
            y = top + h - (int)bh.rec.row[i].pct * h / 100;
        canvas_set_fill_color(canvas, rgba(tone, 0x40));
        canvas_fill_rect(canvas, x, y, next - x, top + h - y);
        canvas_set_fill_color(canvas, rgba(tone, 255));
        canvas_fill_rect(canvas, x, y - 1, next - x + 1, 2);
        if (i + 1 < bh.rec.n) /* the step down to the next row */
            canvas_fill_rect(canvas, next - 1, y - 1, 2,
                             top + h - (int)bh.rec.row[i + 1].pct * h / 100 - y + 2);
        if (i == cur) cx = x, cy = y;
        x = next;
    }
    /* the reading: a hairline from 100% to 0% and the dot on its level */
    canvas_set_fill_color(canvas, rgba(tok(TOK_SECONDARY), 255));
    canvas_fill_rect(canvas, cx, top, 1, h + 1);
    rect_t dot = { cx - BH_DOT / 2, cy - BH_DOT / 2, BH_DOT, BH_DOT };
    fill_box(canvas, &dot, RGBA(0xffffff), BH_DOT / 2);
    bh.button.w = 0;
    if (window_manager_get_top_window(window_manager()) == bh.page) {
        int bw = ui_button(canvas, 375 / 2, BH_BTN_CY - dy, "Share Log", 0, 0, 0);
        bh.button = (rect_t){ 375 / 2 - bw / 2, BH_BTN_CY - CTA_H / 2, bw, CTA_H };
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
}
