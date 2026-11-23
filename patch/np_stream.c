/* Now Playing while a server song loads
 * (docs/server-library.md#download-ahead-and-the-streaming-state; the approved design is
 * streaming-v3's variant D): the 12-spoke spinner and "7.0 of 20 MB" centred in the times row,
 * between the elapsed and the remaining time, which both stay. Loading is the pressed song before
 * the player has it (catalogue_np_now: its own .stream line, else the app's first), or the song
 * playing while q2player waits for more of it (/tmp/q2player.wait). It goes once the song plays on.
 * Under OLED only (the status row's set). Nothing dims, no "Buffering…". The spinner reports
 * progress, so it turns under Reduce Motion too (motion_ms, MO_FEEDBACK). A server song (a card
 * app's stream download, or the pressed catalogue row) also has a format badge at the art's bottom
 * line, on the art's page so a swipe takes it with the heart and More: the file's format in a ring,
 * no glyph (the user's, 2026-11-19). A server song that
 * can't go on while the server is out of reach (pressed and not yet the player's, or one the player
 * waits for) first shows why beside the spinner while the app tries again, "Server error 502 ·
 * trying again" (NS_TRYING_MS), then why alone in the times row in the spinner's place and Retry
 * (selected) and Skip, the empty states' capsule pair, in the state row's place (the user's ruling,
 * 2026-11-19: no silent skip; the app waits at such a song, never skipping it): Retry (a tap, or
 * Centre at rest) asks the app to try again now (/tmp/q2-<id>.retry), Skip plays the next song as
 * Next does. The wheel stays the volume's. */
#include "navigation.h"
#include "ui_kit.h"

#define NS_CHECK_MS 500    /* the state read again */
#define NS_TRYING_MS 10000 /* why, with the spinner, before Retry and Skip */
#define NS_FRAME_MS 83     /* a spoke a frame: one turn a second */
#define NS_SPIN 14         /* tools/art.py NP_SPIN_PX */
#define NS_GAP 6           /* between the spinner and the amount */
/* The times row's centre on the screen (docs/oled-design.md Now Playing). Now Playing's window
 * is the whole screen, and a view's paint coordinates are its own: drawn at NS_Y less
 * the view's y. */
#define NS_Y 250
#define NS_PX 13
#define NB_H 20 /* the badge (np_about.c's quality badge): 20 px high, 6 px corners */
#define NB_R 6
#define NB_GAP 18 /* from the art's right edge: the title's column (tools/ipod.py NP_GRID_GAP) */
#define NB_PAD 9 /* the label from the badge's left, and the ring past its end */

static struct {
    int on, frame;
    unsigned have, total; /* bytes */
    unsigned timer, checked;
    rect_t box;      /* what was painted last, for the next frame's repaint */
    int badge, down; /* a server song shows its badge; its app says server=down (ns.error's) */
    int error;       /* that song can't go on with its server down: the message, Retry and Skip */
    int trying;      /* ... and the app still tries: why beside the spinner (NS_TRYING_MS) */
    unsigned since;  /* when it got stuck, 0 while it isn't */
    char why[32];    /* why, in words: the app's reach= code's, else "Can't reach the server" */
    char id[16];     /* its app */
    char label[8];   /* its format, the file's extension in capitals */
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

/* The path of the song Now Playing shows: the pressed one before the player has it, else the
 * queue's at MCL_POS; 0 for none. */
static const char *ns_path(void) {
    unsigned at, n;
    void *pressed = catalogue_np_now(&at, &n);
    if (pressed) return P(pressed, REC_PATH);
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    return q && pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0;
}

/* Whether path is a server song: a card app's stream download or a catalogue row, its app's id
 * into id. */
static int ns_server(const char *path, char *id, unsigned n) {
    if (!stream_app_of(path, id, n)) return 0;
    const char *rest = path + 10 + strlen(id);
    return tk_str_start_with(rest, "/stream/") || tk_str_start_with(rest, "/cat/");
}

/* The badge's state for the song Now Playing shows: ns.badge, ns.label, ns.down. */
static void ns_badge_read(void) {
    char id[16], file[48], raw[512], v[8], reach[16];
    const char *p = ns_path(), *dot = p ? strrchr(p, '.') : 0;
    ns.badge = p && dot && !strstr(dot, "/") && ns_server(p, id, sizeof id);
    if (!ns.badge) return;
    tk_snprintf(ns.id, sizeof ns.id, "%s", id);
    unsigned i = 0;
    for (++dot; dot[i] && i + 1 < sizeof ns.label; ++i)
        ns.label[i] = dot[i] >= 'a' && dot[i] <= 'z' ? (char)(dot[i] - 32) : dot[i];
    ns.label[i] = 0;
    tk_snprintf(file, sizeof file, "/tmp/q2-%s.state", id);
    v[0] = reach[0] = 0;
    if (state_read(file, raw, sizeof raw))
        field(raw, "server", v, sizeof v), field(raw, "reach", reach, sizeof reach);
    ns.down = tk_str_start_with(v, "down") || tk_str_start_with(reach, "down");
    /* the words addon.c's toast says (reach_read): a proxy's code, no answer, a name not found */
    const char *code = tk_str_start_with(reach, "down ") ? reach + 5 : "";
    if (*code >= '1' && *code <= '9')
        tk_snprintf(ns.why, sizeof ns.why, "Server error %s", code);
    else
        tk_snprintf(ns.why, sizeof ns.why, "%s",
                    !strcmp(code, "none")  ? "Server not answering"
                    : !strcmp(code, "dns") ? "Server not found"
                                           : "Can't reach the server");
}

/* Whether the song Now Playing shows is loading, and its line. */
static int ns_loading(sline_t *l) {
    char id[16];
    unsigned at, n;
    void *pressed = catalogue_np_now(&at, &n);
    if (pressed) /* not yet the player's: its own line (a Next waiting, addon.c), else the app's
                  * first song (a pressed catalogue row, whose path is not the download's) */
        return stream_app_of(P(pressed, REC_PATH), id, sizeof id) &&
               (stream_line(id, P(pressed, REC_PATH), l) || stream_line(id, 0, l)) &&
               (l->state == SL_WAIT || l->state == SL_GET || l->state == SL_STALL);
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    const char *p = q && pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0;
    return p && stream_app_of(p, id, sizeof id) && stream_player_waits(p) &&
           stream_line(id, p, l) && (l->state == SL_GET || l->state == SL_STALL);
}

/* For the car remote (coda_rc_shim.c): whether the song Now Playing shows is loading, and its bytes
 * and length (0 when not). */
int np_stream_loading(unsigned *have, unsigned *total, unsigned *ms) {
    sline_t l;
    int on = ns_loading(&l);
    *have = on ? l.have : 0, *total = on ? l.total : 0, *ms = on ? l.ms : 0;
    return on;
}

/* How sure the length of the song Now Playing shows is (stream_length: 0 the player's stands, 1
 * *ms is the server's, 2 unknown), for the times row, the bar, About and the car remote. Read at
 * most every NS_LEN_MS: each paint asks. */
#define NS_LEN_MS 200
int np_stream_length(unsigned *ms) {
    static struct {
        unsigned key, at, ms;
        int how;
    } c __attribute__((section(".scratch")));
    const char *p = ns_path();
    unsigned now = (unsigned)time_now_ms(), key = p ? fnv(FNV_SEED, (const unsigned char *)p) : 0;
    if (!c.at || key != c.key || now - c.at >= NS_LEN_MS) {
        c.key = key, c.at = now | !now, c.ms = 0;
        c.how = p ? stream_length(p, &c.ms) : 0;
    }
    *ms = c.ms;
    return c.how;
}

static int ns_tick(const void *unused) {
    (void)unused;
    void *win = st.np_win;
    /* off its page, or dark (nothing to show: the files it reads are left alone) */
    if (!win || !g_backlight_status || window_manager_get_top_window(window_manager()) != win) {
        ns.timer = 0, ns.on = 0;
        return 0; /* RET_REMOVE; the next paint, lit, starts it again */
    }
    unsigned now = (unsigned)time_now_ms();
    if (now - ns.checked >= NS_CHECK_MS) {
        sline_t l;
        int on = ns_loading(&l), badge = ns.badge, down = ns.down, error = ns.error;
        char label[sizeof ns.label];
        memcpy(label, ns.label, sizeof label);
        ns_badge_read();
        unsigned at, n;
        const char *p = ns_path();
        /* stuck: the pressed song, or one the player waits for, while its server is down; why
         * beside the spinner while the app tries again, then why alone with Retry and Skip */
        int stuck =
            ns.badge && ns.down && (catalogue_np_now(&at, &n) || (p && stream_player_waits(p)));
        if (!stuck)
            ns.since = 0;
        else if (!ns.since)
            ns.since = now - !now; /* never 0, nor past now */
        ns.error = stuck && now - ns.since >= NS_TRYING_MS;
        ns.trying = stuck && !ns.error;
        if (ns.trying && !on) on = 1, l.have = l.total = 0; /* the spinner, with why beside it */
        if (ns.error) on = 0;                               /* the message in the spinner's place */
        if (error != ns.error)
            widget_invalidate_force(win, (void *)0); /* the times and state rows */
        if (st.np_cover &&
            (badge != ns.badge || down != ns.down || memcmp(label, ns.label, sizeof label)))
            widget_invalidate_force(P(st.np_cover, W_PARENT), (void *)0);
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

/* The badge on the art's page (its widget's own coordinates): bottom-aligned with the art. */
static void ns_badge_paint(void *w, void *canvas) {
    void *art = st.np_cover;
    int px = TYPE(13);
    int x = I(art, W_X) + I(art, W_W) + NB_GAP, y = I(art, W_Y) + I(art, W_H) - NB_H;
    unsigned ink = tok(TOK_SECONDARY), fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int bw = ui_text(canvas, ns.label, 1, px, ink, 1, x + NB_PAD, y + NB_H / 2, 120, 2) + 2 * NB_PAD;
    rect_t r = { x, y, bw, NB_H };
    round_ring(canvas, &r, NB_R, 0xff000000u | ink);
    stock_canvas_set_fill_color(canvas, fill);
}

void np_stream_paint(void *w, void *canvas) {
    if (!w || !st.np_win || !P(canvas, CANVAS_LCD)) return;
    if (st.np_cover && w == P(st.np_cover, W_PARENT)) {
        if (ns.badge && ns.label[0]) ns_badge_paint(w, canvas);
        return;
    }
    if (w != st.np_win) return;
    if (ns.error) {
        ui_text(canvas, ns.why, 0, TYPE(NS_PX), tok(TOK_SECONDARY), 1, 375 / 2 - I(w, W_X),
                NS_Y - I(w, W_Y), 200, 1);
        return;
    }
    if (!ns.timer)
        ns.checked = (unsigned)time_now_ms() - NS_CHECK_MS, ns.timer = timer_add(ns_tick, 0, 0);
    if (!ns.on) return;
    char s[56], a[12], b[12];
    ns_mb(a, sizeof a, ns.have, 1);
    if (ns.trying)
        tk_snprintf(s, sizeof s, "%s \xc2\xb7 trying again", ns.why);
    else if (ns.total)
        ns_mb(b, sizeof b, ns.total, 0), tk_snprintf(s, sizeof s, "%s of %s MB", a, b);
    else
        tk_snprintf(s, sizeof s, "%s MB", a);
    int px = TYPE(NS_PX), tw = ui_text_w(canvas, s, 0, px), gw = NS_SPIN + NS_GAP + tw,
        x = 375 / 2 - I(w, W_X) - gw / 2, y = NS_Y - I(w, W_Y);
    unsigned bitmap[64]; /* bitmap_t */
    char name[20];
    tk_snprintf(name, sizeof name, "npspin_%c_%d", ipod_light() ? 'l' : 'd', ns.frame);
    if (!widget_load_image(w, theme_art(name), bitmap))
        canvas_draw_icon(canvas, bitmap, x + NS_SPIN / 2, y);
    ui_text(canvas, s, 0, px, tok(TOK_SECONDARY), 1, x + NS_SPIN + NS_GAP, y, tw + 2, 2);
    ns.box = (rect_t){ x - 2, y - px, gw + 6, 2 * px };
}

/* Retry and Skip in the state row's place while ns.error (np_bar.c np_modes, np_mode_click; the
 * Centre at rest, now_playing.c np_centre). */
static const char *const ns_cta[] = { "Retry", "Skip" };
int np_stream_error(void) { return ns.error && st.np_win; }
void np_stream_cta_paint(void *canvas, int cy) {
    ui_cta_paint(canvas, ns_cta, 2, cy - CTA_H / 2, 0, 0);
}
/* Retry: the app tries the song again now (its run reads the file and takes it); Skip: Next. */
void np_stream_retry(void) {
    char file[48], tmp[52];
    tk_snprintf(file, sizeof file, "/tmp/q2-%s.retry", ns.id);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", file);
    blob_io(file, tmp, "1\n", 2, 1);
}
/* A tap at screen x on the state row: the capsule it falls on (the pair is centred: left of the
 * middle is Retry). */
void np_stream_cta_click(int x) {
    if (x < 375 / 2)
        np_stream_retry();
    else
        media_key(KEY_FWD_BTN);
}
