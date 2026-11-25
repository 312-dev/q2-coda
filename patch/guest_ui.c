/* Guest Play's screens (docs/oled-design.md#guest-play, docs/internals.md#guest-play-screens), on
 * q2-guest's contract (docs/internals.md#guest-play) and nothing else: its state through guest.c's
 * guest_field and guest_seq, its commands through guest_send. The daemon decides; this file asks
 * the owner and shows what it says:
 * - a guest asking: the confirmation card, Allow Once selected (one Centre answers it), Always
 *   Allow, Decline, counting down to the daemon's own 30 s Decline, the pairing's code large when
 *   the phone compares codes; a second guest while one plays: Keep (selected) or Replace;
 * - the guest playing: its own Now Playing (guestnp_page), the guest's song, where the sound comes
 *   from and goes (both legs' codec lines) and one selectable thing, Stop Guest Play;
 * - what ended or refused a guest: a toast, or for a failed pairing a page with Try Again;
 * - Settings > Bluetooth > Guest Play (guestset_page): the switch, Name, Let a Guest Connect (its
 *   page, guestconnect_page: the name to pick, the window's countdown, the hotspot's QR code),
 *   Allow, When a Guest Leaves, Trusted Guests (each its page, guesttrust_page) and Hotspot
 *   (hotspot_page: its switch, name, password and join QR code).
 * The status row's guest glyph is status_row.c's and Home's first row reading Guest Play
 * home_np.c's, each reading the guest's state. */
#include "navigation.h"

#define GU_POLL_MS 250 /* guest.c reads the state as often */
#define GU_VAL 128     /* a value as read */
#define GU_ROWS 32     /* a settings page's rows at most */
#define GU_TRUSTED 16  /* the contract's Trusted Guests at most */
#define GU_TRUSTED_FILE "/mnt/data/q2-guest/trusted"
#define GU_NAME_MAX 30 /* a name typed on the strip (bt_name.c BT_NAME_MAX) */
#define GU_PASS_MIN 8  /* a WPA password's least characters */
#define GU_PASS_MAX 63
#define GU_NOTE_PX TYPE(13)
/* Now Playing (G5, G12), on the screen */
#define GU_ART 104
#define GU_ART_X 24
#define GU_ART_Y 50
#define GU_ART_R 8
#define GU_TEXT_X 144
#define GU_TEXT_W 207
#define GU_TITLE_Y 66
#define GU_ARTIST_Y 94
#define GU_FROM_Y 122
#define GU_PAUSED_Y 142
#define GU_PATH_Y 178
#define GU_LEG_Y 202
#define GU_LEG_DY 19
#define GU_PILL_Y 244
#define GU_PILL_H 32
#define GU_PILL_PAD 18
#define GU_GLYPH 15
/* Let a Guest Connect (G2) */
#define GC_LEAD_Y 72
#define GC_NAME_Y 104
#define GC_BT_Y 134
#define GC_RING_CX 316
#define GC_RING_CY 106
#define GC_RING_R 26
#define GC_QR_X 24
#define GC_QR_Y 160
#define GC_QR 86
#define GC_SIDE_X 124
/* Hotspot (N13): the code under the rows */
#define HS_QR 86
#define HS_QR_X 24
#define HS_QR_TOP 128
#define HS_SIDE_X 124

enum { G_OFF, G_IDLE, G_OPEN, G_ASKING, G_REPLACE, G_PLAYING, G_PAUSED };
enum { ASK_NONE, ASK_GUEST, ASK_REPLACE };
/* a settings page's rows, by what they do */
enum {
    R_SWITCH,
    R_NAME,
    R_CONNECT,
    R_HEAD,
    R_ASK,
    R_TRUSTED_ONLY,
    R_RESUME,
    R_STAY,
    R_GUEST,
    R_HOTSPOT,
    R_NOTE,
    T_RENAME,
    T_ASK,
    T_FORGET,
    H_SWITCH,
    H_NAME,
    H_PASS
};

static struct {
    unsigned seq, event_seq, poll_at;
    int state, ask;          /* the last state read; the card of ours that is up */
    char ask_mac[40];        /* the guest it asks about */
    char keep[80], body[96]; /* the card's Keep row and body, while it is up */
    char who[64];            /* ... and the guest it names (confirm keeps the pointers) */
    void *np, *set, *set_view, *trust, *trust_view, *hot, *hot_view, *conn, *fail;
    int rows[GU_ROWS], n; /* the settings page's rows */
    int trows[4], tn, hrows[4], hn;
    char fail_name[64];
    struct {
        char mac[40], name[64];
        int ask;
        unsigned played;
    } trusted[GU_TRUSTED];
    int ntrusted, cur; /* the trusted guests read; the one whose page is open */
    int home;          /* a guest playing at the last look: Home's first row reads Guest Play */
} gu __attribute__((section(".scratch")));

/* ---- the state ---- */

/* key's value, in one of four buffers, so a few can be held at once. */
static const char *gv(const char *key) {
    static char buf[4][GU_VAL] __attribute__((section(".scratch")));
    static unsigned at __attribute__((section(".scratch")));
    char *out = buf[at++ % 4];
    guest_field(key, out, GU_VAL);
    return out;
}
static int gv_is(const char *key, const char *value) { return !tk_strcmp(gv(key), value); }
/* key's value into out, for one held past a few more reads (gv's buffers turn over). */
static char *gcopy(const char *key, char *out, unsigned n) {
    guest_field(key, out, n);
    return out;
}

/* The state's word as G_*: no file (the daemon not running) is off. */
int guest_ui_state(void) {
    static const char *const words[] = { "off",     "idle",    "open",  "asking",
                                         "replace", "playing", "paused" };
    const char *s = gv("state");
    for (int i = 0; i < (int)(sizeof words / sizeof *words); ++i)
        if (!tk_strcmp(s, words[i])) return i;
    return G_OFF;
}
/* Guest Play switched on (its saved setting, as the state says it). */
int guest_ui_on(void) { return guest_ui_state() != G_OFF && !gv_is("enabled", "0"); }

/* The owner's short word for a guest (open question 7): "Alex" for "Alex's Pixel", the name up
 * to its first space for others, as the toasts name them. */
void guest_short(const char *name, char *out, unsigned n) {
    const char *s = strstr(name, "'s ");
    unsigned len = s ? (unsigned)(s - name) : (unsigned)strlen(name);
    if (!s)
        for (len = 0; name[len] && name[len] != ' ';) ++len;
    tk_snprintf(out, n, "%.*s", (int)len, name);
}

/* The guest's or the second guest's source, as a phrase: "on Spotify" and so on, "" for a phone. */
static const char *source_words(const char *source) {
    return !tk_strcmp(source, "spotify")   ? " on Spotify"
           : !tk_strcmp(source, "airplay") ? " over AirPlay"
           : !tk_strcmp(source, "dlna")    ? " over DLNA"
                                           : "";
}

/* ---- the prompts (G3, G4, G6) ---- */

static void answered(void *ctx, int row, int toggle_on) {
    (void)toggle_on;
    int ask = (int)(long)ctx;
    gu.ask = ASK_NONE;
    if (ask == ASK_GUEST)
        guest_send(row == 0 ? "allow-once" : row == 1 ? "allow-always" : "decline");
    else
        guest_send(row == 1 ? "replace" : "keep");
}

/* The card for the state asking: the daemon's countdown on it, the safe answer selected, Return
 * the refusal. 1 once shown. */
static int ask_card(int ask) {
    static const char *const guest_rows[] = { "Allow Once", "Always Allow", "Decline" };
    static const char *rows[2] __attribute__((section(".scratch")));
    char code[12];
    const char *guest = gcopy("guest", gu.who, sizeof gu.who);
    gcopy("code", code, sizeof code);
    int secs = atoi(gv("countdown"));
    if (secs <= 0 || secs > 30) secs = 30;
    if (ask == ASK_GUEST) {
        char who[40];
        guest_short(guest, who, sizeof who);
        int digits = strlen(code) == 6;
        tk_snprintf(gu.body, sizeof gu.body,
                    digits ? "Wants to play music%s.\nCheck it's on %s's phone."
                           : "Wants to play music%s.",
                    source_words(gv("source")), who);
        /* the pairing card (pair_prompt.c), as Car Remote's */
        return pair_prompt(guest, digits ? code : 0, gu.body, guest_rows, 3, 2, secs * 1000, 2,
                           answered, (void *)(long)ASK_GUEST) != 0;
    }
    tk_snprintf(gu.keep, sizeof gu.keep, "Keep %s", guest);
    tk_snprintf(gu.body, sizeof gu.body, "%s wants to play music%s", gv("new"),
                source_words(gv("newsource")));
    rows[0] = gu.keep, rows[1] = "Replace";
    return confirm(&(confirm_t){ "Replace %s?", guest, gu.body, 0, rows, 2, CONFIRM_CONSEQUENTIAL,
                                 0, 0, 0, secs * 1000, 0, answered, (void *)(long)ASK_REPLACE, 0, 0, 0 });
}

/* The state's question against the card up: a new one shown, a settled one closed (the daemon
 * answered it: its 30 s, or the guest left). */
static void ask_follow(int state) {
    int want = state == G_ASKING ? ASK_GUEST : state == G_REPLACE ? ASK_REPLACE : ASK_NONE;
    const char *mac = gv(want == ASK_REPLACE ? "newmac" : "mac");
    if (gu.ask && (want != gu.ask || tk_strcmp(mac, gu.ask_mac))) {
        if (confirm_up()) confirm_close();
        gu.ask = ASK_NONE;
    }
    if (want && !gu.ask && ask_card(want)) {
        gu.ask = want;
        tk_snprintf(gu.ask_mac, sizeof gu.ask_mac, "%s", mac);
    }
}

/* ---- what ended a guest: a toast, or the failed pairing's page (G7, G8, G10, G11) ---- */

static int fail_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.fail) gu.fail = (void *)0;
    return 0;
}
/* Try Again: Centre or a tap opens the window again for the guest's phone. */
static int fail_key(void *ctx, void *event) {
    (void)ctx;
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key != KEY_CENTER && key != KEY_RETURN) return 0;
    if (key == KEY_CENTER) guest_send("open");
    window_close(gu.fail);
    return STOP;
}
static int fail_tap(void *ctx, void *event) {
    (void)ctx, (void)event;
    guest_send("open");
    if (gu.fail) window_close(gu.fail);
    return STOP;
}

static void event_follow(void) {
    unsigned seq = (unsigned)atoi(gv("event_seq"));
    if (seq == gu.event_seq) return;
    int first = !gu.event_seq && !gu.seq;
    gu.event_seq = seq;
    if (first) return; /* the state's last event, from before this look: said already */
    char event[16], name[64];
    gcopy("event", event, sizeof event), gcopy("eventname", name, sizeof name);
    char who[40], s[96];
    int resume = !gv_is("leave", "stay");
    guest_short(name, who, sizeof who);
    const char *glyph = "bttoast_phone";
    if (!tk_strcmp(event, "failed")) {
        if (gu.fail || !(gu.fail = page_open("guestfail_page", fail_closed, fail_key))) return;
        widget_on(gu.fail, EVT_CLICK, fail_tap, 0);
        tk_snprintf(gu.fail_name, sizeof gu.fail_name, "%s", who);
        return;
    }
    if (!tk_strcmp(event, "left"))
        tk_snprintf(s, sizeof s, resume ? "%s left. Resuming your music" : "%s left", who);
    else if (!tk_strcmp(event, "lost"))
        tk_snprintf(s, sizeof s, resume ? "Lost %s. Resuming your music" : "Lost %s", who),
            glyph = "bttoast_warn";
    else if (!tk_strcmp(event, "declined") || !tk_strcmp(event, "timeout"))
        tk_snprintf(s, sizeof s, "Declined %s", name), glyph = "bttoast_no";
    else if (!tk_strcmp(event, "refused"))
        tk_snprintf(s, sizeof s, "%s isn't a trusted guest", name), glyph = "bttoast_no";
    else if (!tk_strcmp(event, "stopped"))
        tk_snprintf(s, sizeof s, resume ? "Stopped %s. Resuming your music" : "Stopped %s", who);
    else if (!tk_strcmp(event, "gaveup"))
        tk_snprintf(s, sizeof s,
                    resume ? "%s paused too long. Resuming your music" : "%s paused too long", who);
    else if (!tk_strcmp(event, "kept"))
        tk_snprintf(s, sizeof s, "Turned away %s", name), glyph = "bttoast_no";
    else if (!tk_strcmp(event, "replaced"))
        tk_snprintf(s, sizeof s, "Replaced %s", name);
    else
        return;
    toast_icon(theme_art(glyph), s);
}

/* ---- the guest's Now Playing (G5, G12) ---- */

static int np_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.np) gu.np = (void *)0;
    return 0;
}
/* Centre is Stop Guest Play, at once (the driver-safe rule: no confirm); Play/Pause, Next and
 * Previous are the guest's phone's; Return leaves the page, the guest playing on. */
static int np_key(void *ctx, void *event) {
    (void)ctx;
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key == KEY_CENTER)
        guest_send("stop");
    else if (key == KEY_PLAY)
        guest_send("key toggle");
    else if (key == KEY_NEXT)
        guest_send("key next");
    else if (key == KEY_PREV)
        guest_send("key prev");
    else if (key == KEY_RETURN)
        navigator_back();
    else
        return 0;
    return STOP;
}
static int gnp_tap(void *ctx, void *event) {
    (void)ctx, (void)event;
    guest_send("stop");
    return STOP;
}
/* Home's first row while a guest plays (home_np.c), and the guest starting: the page. */
void guest_np_open(void) {
    if (gu.np || !(gu.np = page_open("guestnp_page", np_closed, np_key))) return;
    widget_on(gu.np, EVT_CLICK, gnp_tap, 0);
}

/* A leg's line from the state's "CODEC RATE KBPS": "LDAC · 96 kHz · 990 kbps", what is known. */
static void leg_words(const char *leg, char *out, unsigned n) {
    char codec[16] = "";
    unsigned rate = 0, kbps = 0, at = 0;
    const char *p = leg;
    while (*p && *p != ' ' && at < sizeof codec - 1) codec[at++] = *p++;
    codec[at] = 0;
    while (*p == ' ') ++p;
    for (; *p >= '0' && *p <= '9'; ++p) rate = rate * 10 + (unsigned)(*p - '0');
    while (*p == ' ') ++p;
    for (; *p >= '0' && *p <= '9'; ++p) kbps = kbps * 10 + (unsigned)(*p - '0');
    at = (unsigned)tk_snprintf(out, n, "%s", codec);
    if (rate && at < n)
        at += rate % 1000 ? (unsigned)tk_snprintf(out + at, n - at, " · %u.%u kHz", rate / 1000,
                                                  rate % 1000 / 100)
                          : (unsigned)tk_snprintf(out + at, n - at, " · %u kHz", rate / 1000);
    if (kbps && at < n) tk_snprintf(out + at, n - at, " · %u kbps", kbps);
}

static void np_paint(void *w, void *canvas) {
    int dy = I(w, W_Y), x = GU_TEXT_X, primary = (int)tok(TOK_PRIMARY);
    unsigned secondary = tok(TOK_SECONDARY), tertiary = tok(TOK_TERTIARY);
    char title[96], artist[96], guest[64], out[64];
    gcopy("title", title, sizeof title), gcopy("artist", artist, sizeof artist);
    gcopy("guest", guest, sizeof guest), gcopy("out", out, sizeof out);
    int paused = guest_ui_state() == G_PAUSED, jack = !*out || !tk_strcmp(out, "jack");
    char s[128], who[40];
    guest_short(guest, who, sizeof who);
    rect_t art = { GU_ART_X, GU_ART_Y - dy, GU_ART, GU_ART };
    fill_box(canvas, &art, RGBA(tok(TOK_RAISED_2)), GU_ART_R);
    paint_art(w, canvas, theme_art("empty_music-notes"), art.x + (GU_ART - 28) / 2,
              art.y + (GU_ART - 28) / 2);
    ui_text(canvas, *title ? title : "Connecting…", 1, TYPE(22), (unsigned)primary, 1, x,
            GU_TITLE_Y - dy, GU_TEXT_W, 2);
    if (*artist)
        ui_text(canvas, artist, 0, TYPE(18), secondary, 1, x, GU_ARTIST_Y - dy, GU_TEXT_W, 2);
    paint_art(w, canvas, theme_art("guestphone_a"), x, GU_FROM_Y - dy - GU_GLYPH / 2);
    tk_snprintf(s, sizeof s, "Playing from %s", guest);
    ui_text(canvas, s, 0, TYPE(15), secondary, 1, x + GU_GLYPH + 4, GU_FROM_Y - dy,
            GU_TEXT_W - GU_GLYPH - 4, 2);
    tk_snprintf(s, sizeof s, paused ? "Paused on %s's phone" : "Your music is paused", who);
    ui_text(canvas, s, 0, TYPE(13), tertiary, 1, x, GU_PAUSED_Y - dy, GU_TEXT_W, 2);
    /* the path, then each leg's codec line */
    tk_snprintf(s, sizeof s, jack ? "%s → Q2 → headphone jack" : "%s → Q2 → %s", guest, out);
    ui_text(canvas, s, 1, TYPE(17), (unsigned)primary, 1, 375 / 2, GU_PATH_Y - dy, 327, 1);
    char leg[48];
    leg_words(gv("in"), leg, sizeof leg);
    tk_snprintf(s, sizeof s, "%s → Q2: %s", guest, leg);
    if (*leg) ui_text(canvas, s, 0, TYPE(14), tertiary, 1, 375 / 2, GU_LEG_Y - dy, 327, 1);
    if (!jack) {
        leg_words(gv("outcodec"), leg, sizeof leg);
        tk_snprintf(s, sizeof s, "Q2 → %s: %s", out, leg);
        if (*leg)
            ui_text(canvas, s, 0, TYPE(14), tertiary, 1, 375 / 2, GU_LEG_Y + GU_LEG_DY - dy, 327,
                    1);
    }
    /* Stop Guest Play, the one selectable thing: selected */
    int pw =
        ui_text(canvas, "Stop Guest Play", 0, TYPE(18), 0, 1, -1000, 0, 327, 2) + 2 * GU_PILL_PAD;
    rect_t pill = { (375 - pw) / 2, GU_PILL_Y - dy, pw, GU_PILL_H };
    fill_box(canvas, &pill, RGBA(scheme()->tones[0] & 0xffffffu), GU_PILL_H / 2);
    ui_text(canvas, "Stop Guest Play", 0, TYPE(18), tok(TOK_ON_ACCENT), 1, 375 / 2,
            pill.y + GU_PILL_H / 2, pw, 1);
}

static void fail_paint(void *w, void *canvas) {
    char line[96];
    tk_snprintf(line, sizeof line, "Try again from %s's phone", gu.fail_name);
    mode_page_paint(
        w, canvas, I(w, W_Y),
        &(mode_page_t){ tok(TOK_WARNING), "modepage_bad", 0, "Couldn't Connect", line, "Try Again" });
}

/* ---- Let a Guest Connect (G2) ---- */

static int conn_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.conn) gu.conn = (void *)0;
    return 0;
}
/* Done (Centre, a tap, Return): the window closes. */
static int conn_key(void *ctx, void *event) {
    (void)ctx;
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key != KEY_CENTER && key != KEY_RETURN) return 0;
    guest_send("close");
    window_close(gu.conn);
    return STOP;
}
static int conn_tap(void *ctx, void *event) {
    (void)ctx, (void)event;
    guest_send("close");
    if (gu.conn) window_close(gu.conn);
    return STOP;
}
static int conn_open(void *ctx, void *event) {
    (void)ctx, (void)event;
    guest_send("open");
    if (gu.conn || !(gu.conn = page_open("guestconnect_page", conn_closed, conn_key))) return 0;
    widget_on(gu.conn, EVT_CLICK, conn_tap, 0);
    return 0;
}

/* The name a phone sees: Guest Play's, else the Q2's Bluetooth name. */
static const char *play_name(char *out, unsigned n) {
    const char *name = gv("name");
    if (*name) return tk_snprintf(out, n, "%s", name), out;
    return bt_name(out, n);
}

static void conn_paint(void *w, void *canvas) {
    int dy = I(w, W_Y), x = geom_val(V_TEXT_X);
    unsigned secondary = tok(TOK_SECONDARY), tertiary = tok(TOK_TERTIARY),
             primary = tok(TOK_PRIMARY);
    char name[64], s[64];
    ui_text(canvas, "On your phone, connect to", 0, TYPE(16), secondary, 1, x, GC_LEAD_Y - dy, 260,
            2);
    ui_text(canvas, play_name(name, sizeof name), 1, TYPE(30), primary, 1, x, GC_NAME_Y - dy, 250,
            2);
    paint_art(w, canvas, theme_art("bttoast_bt_off"), x, GC_BT_Y - dy - GU_GLYPH / 2);
    ui_text(canvas, "in Bluetooth settings", 0, TYPE(16), secondary, 1, x + GU_GLYPH + 6,
            GC_BT_Y - dy, 240, 2);
    /* the window's countdown: a ring emptying over its two minutes, m:ss in it */
    int left = atoi(gv("window"));
    void *vg = canvas_get_vgcanvas(canvas);
    if (vg && left > 0) {
        float cx = (float)GC_RING_CX, cy = (float)(GC_RING_CY - dy);
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        vgcanvas_set_line_width(vg, 3);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, cy, GC_RING_R, -1.5707963f,
                     -1.5707963f + 6.2831853f * (float)(left > 120 ? 120 : left) / 120, 0);
        vgcanvas_set_stroke_color(vg, RGBA(scheme()->tones[0] & 0xffffffu));
        vgcanvas_stroke(vg);
        vgcanvas_restore(vg);
    }
    tk_snprintf(s, sizeof s, "%d:%02d", left / 60, left % 60);
    ui_text(canvas, s, 0, TYPE(16), primary, 1, GC_RING_CX, GC_RING_CY - dy, 60, 1);
    /* AirPlay and Spotify guests: the hotspot's code, while it runs */
    if (gv_is("hotspot_up", "1")) {
        ui_qr(canvas, gv("hotspot_qr"), GC_QR_X, GC_QR_Y - dy, GC_QR);
        ui_text(canvas, "AirPlay or Spotify?", 1, TYPE(17), primary, 1, GC_SIDE_X,
                GC_QR_Y + 14 - dy, 227, 2);
        ui_text(canvas, "Scan to join the Q2's Wi-Fi,", 0, TYPE(14), tertiary, 1, GC_SIDE_X,
                GC_QR_Y + 40 - dy, 227, 2);
        tk_snprintf(s, sizeof s, "then pick %s", name);
        ui_text(canvas, s, 0, TYPE(14), tertiary, 1, GC_SIDE_X, GC_QR_Y + 58 - dy, 227, 2);
    }
    int pw = 2 * 54;
    rect_t pill = { (375 - pw) / 2, 268 - dy, pw, GU_PILL_H };
    fill_box(canvas, &pill, RGBA(scheme()->tones[0] & 0xffffffu), GU_PILL_H / 2);
    ui_text(canvas, "Done", 0, TYPE(18), tok(TOK_ON_ACCENT), 1, 375 / 2, pill.y + GU_PILL_H / 2, pw,
            1);
}

/* The guest's Now Playing, while a guest plays: 1 when it opened (Home's first row: home_np.c's
 * click, nav/input.c's Return). */
int guest_home_np(void) {
    if (!guest_active()) return 0;
    guest_np_open();
    return 1;
}

/* ---- the settings pages ---- */

static void trusted_read(void) {
    char raw[GU_TRUSTED * 128 + 2];
    gu.ntrusted = 0;
    if (!state_read(GU_TRUSTED_FILE, raw, sizeof raw)) return;
    for (char *line = raw + 1; *line && gu.ntrusted < GU_TRUSTED;) {
        char *end = line, *f[4] = { 0 };
        while (*end && *end != '\n') ++end;
        if (!*end) end = 0;
        if (end) *end = 0;
        if (tsv_split(line, f, 4) == 4) {
            int i = gu.ntrusted++;
            tk_snprintf(gu.trusted[i].mac, sizeof gu.trusted[i].mac, "%s", f[0]);
            gu.trusted[i].ask = atoi(f[1]);
            gu.trusted[i].played = (unsigned)atoi(f[2]);
            tk_snprintf(gu.trusted[i].name, sizeof gu.trusted[i].name, "%s", f[3]);
        }
        if (!end) break;
        line = end + 1;
    }
}

/* A section header or a note row: a disabled row in Text Tertiary's smaller type. */
static void *quiet_row(void *view, int i, const char *text, int px) {
    void *item = page_row_detail(view, i, text, 0, (int (*)(void *, void *))0);
    void *l = widget_get_child(item, 0);
    face_font(l, theme()->font[ROLE_META], px, FONT_ROW);
    text_ink(l, 0xff000000u | tok(TOK_TERTIARY), INK_LABEL);
    widget_set_enable(item, 0);
    return item;
}
/* A choice's check on the right column (bt_ui.c's mark). */
static void check(void *item, int on) {
    if (on)
        bt_mark(item, "_gu_check", BT_MARK_CHECK, 375 - geom_val(V_RIGHT_X) - 20, 0, 20,
                I(item, W_H));
}

static int set_click(void *ctx, void *event);
static void set_fill(void);

static const char *name_done(const char *text) {
    char name[GU_NAME_MAX + 1], line[64];
    unsigned a = 0, b = strlen(text);
    while (text[a] == ' ') ++a;
    while (b > a && text[b - 1] == ' ') --b;
    tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), text + a);
    if (*name && !bt_name_valid(name)) return "Can't use that name";
    tk_snprintf(line, sizeof line, "name %s", name);
    guest_send(line);
    toast("Renamed");
    return 0;
}

static void set_fill(void) {
    if (!gu.set) return;
    trusted_read();
    int on = guest_ui_on(), n = 0,
        sel = gu.set_view ? widget_get_prop_int(gu.set_view, "_ringnav_index", 0) : 0;
    gu.rows[n++] = R_SWITCH;
    if (on) {
        gu.rows[n++] = R_NAME;
        gu.rows[n++] = R_CONNECT;
        gu.rows[n++] = R_HEAD, gu.rows[n++] = R_ASK, gu.rows[n++] = R_TRUSTED_ONLY;
        gu.rows[n++] = R_HEAD, gu.rows[n++] = R_RESUME, gu.rows[n++] = R_STAY;
        if (gu.ntrusted) gu.rows[n++] = R_HEAD;
        for (int i = 0; i < gu.ntrusted && n < GU_ROWS - 3; ++i) gu.rows[n++] = R_GUEST + 100 * i;
        gu.rows[n++] = R_HOTSPOT;
    }
    gu.rows[n++] = R_NOTE;
    gu.n = n;
    void *view = gu.set_view = page_list(gu.set, gu.set, 0, "Guest Play", n, 48);
    int head = 0, guest = 0;
    for (int i = 0; i < n; ++i) {
        int r = gu.rows[i] % 100;
        char s[64];
        void *item;
        switch (r) {
        case R_SWITCH:
            item = page_row_detail(view, i, "Guest Play", 0, set_click);
            list_toggle(widget_get_child(item, 0), "Guest Play", on);
            break;
        case R_NAME:
            item = page_row_detail_into(view, i, "Name", 0, set_click);
            list_value(widget_get_child(item, 0), "Name", play_name(s, sizeof s));
            break;
        case R_CONNECT:
            page_row_detail_into(view, i, "Let a Guest Connect", 0, set_click);
            break;
        case R_HEAD: {
            static const char *const heads[] = { "Allow", "When a Guest Leaves", "Trusted Guests" };
            quiet_row(view, i, heads[head++ % 3], TYPE(15));
            break;
        }
        case R_ASK:
        case R_TRUSTED_ONLY:
            item = page_row_detail(view, i, r == R_ASK ? "Ask Every Time" : "Trusted Guests Only",
                                   0, set_click);
            check(item, gv_is("allow", "trusted") == (r == R_TRUSTED_ONLY));
            break;
        case R_RESUME:
        case R_STAY:
            item = page_row_detail(view, i, r == R_RESUME ? "Resume My Music" : "Stay Paused", 0,
                                   set_click);
            check(item, gv_is("leave", "stay") == (r == R_STAY));
            break;
        case R_GUEST:
            page_row_detail_into(view, i, gu.trusted[guest++].name, 0, set_click);
            break;
        case R_HOTSPOT:
            item = page_row_detail_into(view, i, "Hotspot", 0, set_click);
            list_value(widget_get_child(item, 0), "Hotspot", gv_is("hotspot", "1") ? "On" : "Off");
            break;
        case R_NOTE:
            quiet_row(view, i,
                      on ? "Guests play through the Q2 to what it plays to."
                         : "Turn on to let a friend's phone play through the Q2.",
                      GU_NOTE_PX);
            break;
        }
    }
    ringnav_select(view, sel < n ? sel : 0, n);
}

static int set_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.set) gu.set = gu.set_view = (void *)0;
    return 0;
}
/* Settings > Bluetooth's Guest Play row: the page. */
int guest_settings_open(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (gu.set || !(gu.set = page_open("guestset_page", set_closed, page_keyup))) return 0;
    gu.set_view = 0;
    set_fill();
    return 0;
}

static void trust_fill(void);
static int trust_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.trust) gu.trust = gu.trust_view = (void *)0;
    return 0;
}
static int hot_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == gu.hot) gu.hot = gu.hot_view = (void *)0;
    return 0;
}
static void hot_fill(void);

static int set_click(void *ctx, void *event) {
    (void)event;
    int i = (int)(long)ctx, r = i >= 0 && i < gu.n ? gu.rows[i] : -1;
    char s[64];
    switch (r % 100) {
    case R_SWITCH:
        guest_send(guest_ui_on() ? "off" : "on");
        break;
    case R_NAME:
        search_strip_note("Guests and AirPlay see this name");
        search_strip_label("Name"); /* typed in its row, after the label (search_strip.c) */
        search_strip_return(STRIP_SAVE);
        search_strip_text("Guest Play", play_name(s, sizeof s), GU_NAME_MAX, name_done);
        break;
    case R_CONNECT:
        conn_open(0, 0);
        break;
    case R_ASK:
        guest_send("allow ask");
        break;
    case R_TRUSTED_ONLY:
        guest_send("allow trusted");
        break;
    case R_RESUME:
        guest_send("leave resume");
        break;
    case R_STAY:
        guest_send("leave stay");
        break;
    case R_GUEST:
        gu.cur = r / 100;
        if (!gu.trust && (gu.trust = page_open("guesttrust_page", trust_closed, page_keyup)))
            gu.trust_view = 0, trust_fill();
        break;
    case R_HOTSPOT:
        if (!gu.hot && (gu.hot = page_open("hotspot_page", hot_closed, page_keyup)))
            gu.hot_view = 0, hot_fill();
        break;
    }
    return 0;
}

/* ---- a trusted guest's page (N12) ---- */

static int trust_click(void *ctx, void *event);
static void trust_fill(void) {
    if (!gu.trust || gu.cur >= gu.ntrusted) return;
    int sel = gu.trust_view ? widget_get_prop_int(gu.trust_view, "_ringnav_index", 0) : 0;
    char s[64];
    void *view = gu.trust_view = page_list(gu.trust, gu.trust, 0, gu.trusted[gu.cur].name, 5, 48);
    void *item = page_row_detail_into(view, 0, "Rename", 0, trust_click);
    guest_short(gu.trusted[gu.cur].name, s, sizeof s);
    list_value(widget_get_child(item, 0), "Rename", s);
    item = page_row_detail(view, 1, "Ask Before Playing", 0, trust_click);
    list_toggle(widget_get_child(item, 0), "Ask Before Playing", gu.trusted[gu.cur].ask);
    page_row_detail(view, 2, "Forget", 0, trust_click);
    quiet_row(view, 3, "Trusted guests play without a prompt.", GU_NOTE_PX);
    ringnav_select(view, sel < 3 ? sel : 0, 3);
}
static const char *trust_renamed(const char *text) {
    char line[128];
    if (!*text || !bt_name_valid(text)) return "Can't use that name";
    tk_snprintf(line, sizeof line, "rename %s %s", gu.trusted[gu.cur].mac, text);
    guest_send(line);
    toast("Renamed");
    return 0;
}
static void forgot(void *ctx, int row, int toggle_on) {
    (void)ctx, (void)toggle_on;
    char line[64];
    if (row != 1 || gu.cur >= gu.ntrusted) return;
    tk_snprintf(line, sizeof line, "forget %s", gu.trusted[gu.cur].mac);
    guest_send(line);
    if (gu.trust) window_close(gu.trust);
}
static int trust_click(void *ctx, void *event) {
    (void)event;
    static const char *const rows[] = { "Cancel", "Forget" };
    char line[64];
    switch ((int)(long)ctx) {
    case 0:
        search_strip_label("Rename");
        search_strip_return(STRIP_SAVE); /* the return key's check */
        search_strip_text("Rename", gu.trusted[gu.cur].name, GU_NAME_MAX, trust_renamed);
        break;
    case 1:
        tk_snprintf(line, sizeof line, "ask %s %d", gu.trusted[gu.cur].mac,
                    !gu.trusted[gu.cur].ask);
        guest_send(line);
        break;
    case 2:
        confirm(&(confirm_t){ "Forget %s?", gu.trusted[gu.cur].name,
                              "They'll be asked before playing again.", 0, rows, 2,
                              CONFIRM_DESTRUCTIVE, -1, 0, 0, 0, 0, forgot, 0, 0, 0, 0 });
        break;
    }
    return 0;
}

/* ---- Hotspot (N13) ---- */

static int hot_click(void *ctx, void *event);
static void hot_fill(void) {
    if (!gu.hot) return;
    int sel = gu.hot_view ? widget_get_prop_int(gu.hot_view, "_ringnav_index", 0) : 0;
    void *view = gu.hot_view = page_list(gu.hot, gu.hot, 0, "Hotspot", 3, 48);
    int on = gv_is("hotspot", "1");
    void *item = page_row_detail(view, 0, "Hotspot", 0, hot_click);
    list_toggle(widget_get_child(item, 0), "Hotspot", on);
    item = page_row_detail_into(view, 1, "Name", 0, hot_click);
    list_value(widget_get_child(item, 0), "Name", gv("hotspot_name"));
    item = page_row_detail_into(view, 2, "Password", 0, hot_click);
    list_value(widget_get_child(item, 0), "Password", gv("hotspot_pass"));
    ringnav_select(view, sel < 3 ? sel : 0, 3);
}
static const char *pass_done(const char *text) {
    char line[96];
    unsigned n = strlen(text);
    if (n < GU_PASS_MIN) return "At least 8 characters";
    if (!bt_name_valid(text)) return "Can't use that password";
    tk_snprintf(line, sizeof line, "hotspot-pass %s", text);
    guest_send(line);
    return 0;
}
static int hot_click(void *ctx, void *event) {
    (void)event;
    char s[64];
    switch ((int)(long)ctx) {
    case 0:
        guest_send(gv_is("hotspot", "1") ? "hotspot off" : "hotspot on");
        break;
    case 1: /* the hotspot is named as Guest Play is */
        search_strip_note("Guests and AirPlay see this name");
        search_strip_label("Name"); /* typed in its row, after the label (search_strip.c) */
        search_strip_return(STRIP_SAVE);
        search_strip_text("Guest Play", play_name(s, sizeof s), GU_NAME_MAX, name_done);
        break;
    case 2:
        search_strip_note("Guests type it to join the Q2's Wi-Fi");
        search_strip_secret(); /* dots on the strip, as every password's */
        search_strip_return(STRIP_GO);
        search_strip_text("Hotspot", gv("hotspot_pass"), GU_PASS_MAX, pass_done);
        break;
    }
    return 0;
}
static void hot_paint(void *w, void *canvas) {
    int dy = I(w, W_Y), top = HS_QR_TOP + 58 - dy;
    unsigned primary = tok(TOK_PRIMARY), tertiary = tok(TOK_TERTIARY);
    if (!gv_is("hotspot_up", "1")) {
        ui_text(canvas,
                gv_is("hotspot", "1") ? "Starts while Guest Play is on"
                                      : "For AirPlay, Spotify Connect and DLNA guests",
                0, TYPE(13), tertiary, 1, geom_val(V_TEXT_X), top + 10, 327, 2);
        return;
    }
    ui_qr(canvas, gv("hotspot_qr"), HS_QR_X, top, HS_QR);
    ui_text(canvas, "Scan to join", 1, TYPE(17), primary, 1, HS_SIDE_X, top + 14, 227, 2);
    ui_text(canvas, "Then play from AirPlay,", 0, TYPE(14), tertiary, 1, HS_SIDE_X, top + 38, 227,
            2);
    ui_text(canvas, "Spotify or a DLNA app", 0, TYPE(14), tertiary, 1, HS_SIDE_X, top + 56, 227, 2);
}

/* ---- each look ---- */

/* bt_ui.c's Bluetooth page: its Audio group's Guest Play row's value. */
/* Smart Awake (navigation.c keep_lit_poll): the guest's Now Playing in front (top) while the
 * guest's song plays holds the screen lit, as the owner's Now Playing does. */
int guest_ui_keeps_lit(void *top) { return top && top == gu.np && guest_ui_state() == G_PLAYING; }

const char *guest_ui_value(void) {
    return !guest_ui_on() ? "Off" : gv_is("allow", "trusted") ? "Trusted" : "Ask";
}

/* Every UI loop pass (bt_ui_poll): the state against what is shown. */
void guest_ui_poll(void) {
    unsigned now = (unsigned)time_now_ms(), seq;
    if (now - gu.poll_at < GU_POLL_MS) return;
    gu.poll_at = now;
    seq = guest_seq();
    if (seq == gu.seq) {
        if (gu.ask && !confirm_up()) gu.ask = ASK_NONE; /* answered (answered() ran) */
        return;
    }
    int was = gu.state, state = guest_ui_state(), playing = state == G_PLAYING || state == G_PAUSED;
    if (playing != gu.home) { /* Home's first row: Guest Play, or the owner's again (home_np.c) */
        gu.home = playing;
        coverflow_home_layout();
    }
    event_follow();
    gu.seq = seq, gu.state = state;
    ask_follow(state);
    if (playing && was != G_PLAYING && was != G_PAUSED && !*gv("source")) guest_np_open();
    if (!playing && gu.np) window_close(gu.np);
    if (gu.np) widget_invalidate_force(gu.np, (void *)0);
    if (gu.conn) {
        if (state != G_OPEN && !atoi(gv("window")))
            window_close(gu.conn);
        else
            widget_invalidate_force(gu.conn, (void *)0);
    }
    if (gu.set) set_fill();
    if (gu.trust) trusted_read(), trust_fill();
    if (gu.hot) hot_fill(), widget_invalidate_force(gu.hot, (void *)0);
}

/* ringnav_paint, after w's children: the pages this file paints. */
void guest_ui_paint(void *w, void *canvas) {
    if (!w || (w != gu.np && w != gu.conn && w != gu.hot && w != gu.fail)) return;
    if (window_manager_is_animating(window_manager())) return;
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    st.raw_ink = 1;
    if (w == gu.np)
        np_paint(w, canvas);
    else if (w == gu.conn)
        conn_paint(w, canvas);
    else if (w == gu.hot)
        hot_paint(w, canvas);
    else
        fail_paint(w, canvas);
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* The bar's hidden title on these pages; 0 for another. */
const char *guest_ui_title(const char *name) {
    if (!tk_strcmp(name, "guestnp_page") || !tk_strcmp(name, "guestconnect_page") ||
        !tk_strcmp(name, "guestset_page") || !tk_strcmp(name, "guestfail_page"))
        return "Guest Play";
    if (!tk_strcmp(name, "hotspot_page")) return "Hotspot";
    if (!tk_strcmp(name, "guesttrust_page") && gu.cur < gu.ntrusted) return gu.trusted[gu.cur].name;
    return (const char *)0;
}
