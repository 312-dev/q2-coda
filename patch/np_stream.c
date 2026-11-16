/* Now Playing while a server song loads
 * (docs/server-library.md#download-ahead-and-the-streaming-state; the approved design is
 * streaming-v3's variant D): the 12-spoke spinner and "7.0 of 20 MB" centred in the times row,
 * between the elapsed and the remaining time, which both stay. Loading is the pressed song before
 * the player has it (catalogue_np_now: the app's first .stream line), or the song playing while
 * q2player waits for more of it
 * (/tmp/q2player.wait). It goes once the song plays on. Under OLED only (the status row's set).
 * Nothing dims, no "Buffering…". The spinner reports progress, so it turns under Reduce Motion too
 * (motion_ms, MO_FEEDBACK). */
#include "navigation.h"
#include "ui_kit.h"

#define NS_CHECK_MS 500 /* the state read again */
#define NS_FRAME_MS 83  /* a spoke a frame: one turn a second */
#define NS_SPIN 14      /* tools/art.py NP_SPIN_PX */
#define NS_GAP 6        /* between the spinner and the amount */
#define NS_Y 250        /* the times row's centre (docs/oled-design.md Now Playing) */
#define NS_PX 13

static struct {
    int on, frame;
    unsigned have, total; /* bytes */
    unsigned timer, checked;
    rect_t box; /* what was painted last, for the next frame's repaint */
} ns __attribute__((section(".scratch")));

/* MB of 10^6 bytes, tenths below 10 MB and for what has come, as Storage counts (no 64-bit
 * division). */
static void ns_mb(char *s, unsigned n, unsigned bytes, int tenths) {
    unsigned t = bytes / 100000;
    if (tenths || t < 100)
        tk_snprintf(s, n, "%u.%u", t / 10, t % 10);
    else
        tk_snprintf(s, n, "%u", (t + 5) / 10);
}

/* Whether the song Now Playing shows is loading, and its line. */
static int ns_loading(sline_t *l) {
    char id[16];
    unsigned at, n;
    void *pressed = catalogue_np_now(&at, &n);
    if (pressed) /* not yet the player's: the app's first song */
        return stream_app_of(P(pressed, REC_PATH), id, sizeof id) && stream_line(id, 0, l) &&
               (l->state == SL_WAIT || l->state == SL_GET || l->state == SL_STALL);
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    const char *p = q && pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0;
    return p && stream_app_of(p, id, sizeof id) && stream_player_waits(p) &&
           stream_line(id, p, l) && (l->state == SL_GET || l->state == SL_STALL);
}

static int ns_tick(const void *unused) {
    (void)unused;
    void *win = st.np_win;
    if (!win || window_manager_get_top_window(window_manager()) != win) {
        ns.timer = 0, ns.on = 0;
        return 0; /* RET_REMOVE; the next paint starts it again */
    }
    unsigned now = (unsigned)time_now_ms();
    if (now - ns.checked >= NS_CHECK_MS) {
        sline_t l;
        int on = ns_loading(&l);
        ns.checked = now;
        if (on != ns.on || (on && (l.have != ns.have || l.total != ns.total))) {
            ns.on = on, ns.have = on ? l.have : 0, ns.total = on ? l.total : 0;
            widget_invalidate_force(win, ns.box.w ? &ns.box : (void *)0);
        }
    }
    if (ns.on) {
        ns.frame = (ns.frame + 1) % 12;
        widget_invalidate_force(win, &ns.box);
    }
    int ms = ns.on ? motion_ms(NS_FRAME_MS, MO_FEEDBACK) : NS_CHECK_MS;
    ns.timer = timer_add(ns_tick, 0, ms > 0 ? ms : NS_CHECK_MS);
    return 0;
}

void np_stream_paint(void *w, void *canvas) {
    if (!w || w != st.np_win || !geom_val(V_STATUS_Y) || !P(canvas, CANVAS_LCD)) return;
    if (!ns.timer)
        ns.checked = (unsigned)time_now_ms() - NS_CHECK_MS, ns.timer = timer_add(ns_tick, 0, 0);
    if (!ns.on) return;
    char s[40], a[12], b[12];
    ns_mb(a, sizeof a, ns.have, 1);
    if (ns.total)
        ns_mb(b, sizeof b, ns.total, 0), tk_snprintf(s, sizeof s, "%s of %s MB", a, b);
    else
        tk_snprintf(s, sizeof s, "%s MB", a);
    int px = TYPE(NS_PX), tw = ui_text_w(canvas, s, 0, px), gw = NS_SPIN + NS_GAP + tw,
        x = 375 / 2 - gw / 2;
    unsigned bitmap[64]; /* bitmap_t */
    char name[20];
    tk_snprintf(name, sizeof name, "npspin_%c_%d", ipod_light() ? 'l' : 'd', ns.frame);
    if (!widget_load_image(w, theme_art(name), bitmap))
        canvas_draw_icon(canvas, bitmap, x + NS_SPIN / 2, NS_Y);
    ui_text(canvas, s, 0, px, tok(TOK_SECONDARY), 1, x + NS_SPIN + NS_GAP, NS_Y, tw + 2, 2);
    ns.box = (rect_t){ x - 2, NS_Y - px, gw + 6, 2 * px };
}
