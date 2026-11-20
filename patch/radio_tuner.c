/* Radio (docs/internals.md#radio-home, docs/oled-design.md#radio): Home's row under
 * the Library. The stations that reach the place, from q2radiofetch (radiofetch.c), laid out at
 * their real frequencies on an FM dial under a fixed needle; the wheel tunes in 0.2 MHz steps, the
 * needle glides to the step, and the sound is q2video -t's FM receiver (video.c, fmrx.c): hiss
 * between stations, a station's stream swelling out of it as the needle closes on it. The approved
 * prototype
 * (~/q2-emu/design/radio-tuner/index.html) is the spec, screen for screen.
 *
 * Screens: loading, the tuner, the stations list (Centre), the ZIP entry (the list's last row) and
 * the error. The state is on the heap while the page is open; the scratch holds its pointer and the
 * fetch's pid, reaped after the page has gone. */
#include "navigation.h"

#define FETCH_BIN "/usr/bin/q2radiofetch"
#define LIST_FILE "/tmp/q2tuner.list" /* q2radiofetch --out: the list, whole or not at all */
#define ZIP_FILE                                                                                   \
    "/mnt/mmc/Radio/.local/zip"        /* the ZIP the user chose; none: the network's place        \
                                        */
#define TUNER_SOCK "/tmp/q2tuner.sock" /* video.c's */
#define TUNER_STATE "/tmp/q2tuner.state"
#define RT_MAX 64 /* FM stations a list holds */
#define RT_URL 300
#define LO 879 /* the dial's ends, in 100 kHz */
#define HI 1079
#define PX_MHZ 62         /* the dial's scale */
#define DIAL_Y 270        /* its baseline, screen y */
#define RT_GLIDE_MS 60.0f /* the needle's time constant toward the dial's step */
#define LAND_MS 350       /* the needle at rest this long before a stream starts */
#define WARM_MS 4000      /* a stream left playing this long after the dial leaves its station */
#define FRAME_MS 16       /* a repaint while something moves */
#define STATE_MS 500      /* q2video's state read this often */
#define QUIT_MS 1500      /* q2video's own exit, before it is killed */
#define ROW 32            /* the list's rows, from LIST_TOP */
#define LIST_TOP 58
#define LIST_ROWS 7
#define FADE 48 /* the dial's end fades */

enum { LOADING, TUNER, LIST, ZIP, ERROR };

typedef struct {
    short freq; /* 100 kHz */
    unsigned char db;
    float km;
    char call[8], name[48], city[40], url[RT_URL];
} rt_station;

static struct rt {
    void *page, *sheet, *title;
    int screen, n, freq, playing, y0, sel, zi, feed;
    float from; /* the needle's glide: from, toward freq, since glide_at */
    unsigned glide_at, fetch_at, anim, land, warm, polled, shown_at, leave,
        ended;            /* ended: the receiver's */
    float scroll, target; /* the list's, px */
    int zip[5], audio, sock, volume;
    int touch, tx, tf, moved; /* a finger on the dial or the list */
    int no_wifi;              /* the Wi-Fi link is down: "No Wi-Fi" stands (toast_stay) */
    char place[48], err[64], title_text[256], state[12], zip_text[8];
    rt_station st[RT_MAX];
} *rt __attribute__((section(".scratch")));
static int rt_fetch_pid __attribute__((section(".scratch")));

static float needle(void) {
    if (!rt->glide_at) return (float)rt->freq;
    float d = (rt->from - rt->freq) *
              (float)pow(2.718281828, -(double)(time_now_ms() - rt->glide_at) / RT_GLIDE_MS);
    if (d < 0.02f && d > -0.02f)
        return rt->glide_at = 0, (float)rt->freq; /* landed: within 2 kHz */
    return rt->freq + d;
}

static char *rt_chr(const char *s, int c) { /* demo imports no strchr */
    for (; *s; ++s)
        if (*s == c) return (char *)s;
    return 0;
}

static rt_station *at(int freq) {
    for (int i = 0; i < rt->n; ++i)
        if (rt->st[i].freq == freq) return &rt->st[i];
    return 0;
}
static int streams(const rt_station *s) { return s && s->url[0]; }
static int index_of(const rt_station *s) { return s ? (int)(s - rt->st) : -1; }

/* ---- the receiver: q2video -t, told the carrier and the needle ---- */

static void rt_send(const char *s) { dgram_send(&rt->sock, TUNER_SOCK, s, strlen(s)); }

/* The multipath depth for a station km away: next to none within 5 km, most of it at 45. */
static float flutter(float km) {
    float x = (km - 5) / 40;
    return 0.03f + 0.8f * (x < 0 ? 0 : x > 1 ? 1 : x);
}

/* The receiver's carrier and the needle against it: a station with no stream is an unmodulated
 * carrier at the edge of reception (9 dB, flutter 0.7), so its channel sputters; else the loaded
 * stream's station, however far the dial has gone from it (the IF filter makes a far one silent;
 * q2video holds its carrier off until its sound comes); else nothing, the empty channel's hiss. */
static void receive(void) {
    rt_station *here = at(rt->freq), *c = 0;
    float level = -40, fade = 0;
    int id = 0;
    if (here && !streams(here))
        c = here, level = 9, fade = 0.7f, id = -here->freq;
    else if (rt->feed >= 0)
        c = &rt->st[rt->feed], level = c->db, fade = flutter(c->km), id = c->freq;
    char p[96]; /* ints: dB, kHz, the fade in thousandths, the gain, the station (video.c tuner_key)
                 */
    tk_snprintf(p, sizeof p, "p%d %d %d %d %d", (int)level,
                c ? (int)((needle() - c->freq) * 100) : 0, (int)(fade * 1000),
                rt->playing && rt->screen != LOADING ? 1 : 0, id);
    rt_send(p);
}

/* The station whose stream the receiver should have: the one on the dial, or on an empty channel
 * the stronger of its neighbours 0.2 MHz either side at 70 dB or more, which splatters in. */
static int source(void) {
    rt_station *here = at(rt->freq);
    if (here) return streams(here) ? index_of(here) : -1;
    rt_station *a = at(rt->freq - 2), *b = at(rt->freq + 2), *best = 0;
    if (streams(a) && a->db >= 70) best = a;
    if (streams(b) && b->db >= 70 && (!best || b->db > best->db || index_of(b) == rt->feed))
        best = b;
    if (best && index_of(a) == rt->feed && streams(a) && a->db >= 70) best = a;
    return index_of(best);
}

static int land(const void *unused) {
    rt->land = 0;
    int s = source();
    if (s >= 0 && s != rt->feed && rt->playing) {
        char u[RT_URL + 2];
        tk_snprintf(u, sizeof u, "u%s", rt->st[s].url);
        rt_send(u);
        rt->feed = s;
        rt->title_text[0] = 0;
        receive();
    }
    return 0;
}

static int cool(const void *unused) { /* the dial left the loaded stream's station WARM_MS ago */
    rt->warm = 0;
    if (rt->feed >= 0 && source() != rt->feed)
        rt_send("x"), rt->feed = -1, rt->title_text[0] = 0, receive();
    return 0;
}

static int frame(const void *unused);

static void animate(void) {
    if (!rt->anim) rt->anim = timer_add(frame, 0, FRAME_MS);
    widget_invalidate_force(rt->sheet, 0);
}

/* The dial moved to rt->freq (or play or pause): the needle glides there, the stream it lands on
 * starts LAND_MS later, a stream the dial left stays warm WARM_MS. */
static void tune(void) {
    if (!rt->audio && time_now_ms() - rt->ended > 3000) /* gone: started again, at most every 3 s */
        rt->audio = video_start("-", 2), rt->volume = 0;
    float n = needle();
    if ((float)rt->freq != n) rt->from = n, rt->glide_at = time_now_ms();
    int s = rt->playing ? source() : -1;
    if (!rt->playing && rt->feed >= 0) rt_send("x"), rt->feed = -1;
    stop_timer(&rt->land);
    if (s >= 0 && s == rt->feed)
        stop_timer(&rt->warm);
    else {
        if (rt->feed >= 0 && !rt->warm) rt->warm = timer_add(cool, 0, WARM_MS);
        if (s >= 0)
            rt->land = timer_add(land, 0, LAND_MS); /* turning through stations starts none */
    }
    receive();
    animate();
}

/* ---- the list from q2radiofetch ---- */

static int digits(const char *s, int *out) { /* "93.1" as 931; its length */
    int v = 0, k = 0, frac = -1;
    for (; (s[k] >= '0' && s[k] <= '9') || s[k] == '.'; ++k)
        if (s[k] == '.')
            frac = 0;
        else if (frac < 1)
            v = v * 10 + s[k] - '0', frac += frac >= 0;
    *out = frac < 0 ? v * 10 : v;
    return k;
}

/* LIST_FILE's FM stations into rt (radiofetch_core.c rf_text's lines: call, band, MHz, km, dB,
 * name, city, then source=url), and the place: the nearest station's city. 1 when it held any. */
static int list_read(void) {
    void *f = fopen(LIST_FILE, "rb");
    if (!f) return 0;
    char *text = calloc(1, 96 << 10);
    unsigned n = text ? fread(text, 1, (96 << 10) - 1, f) : 0;
    fclose(f);
    if (!text) return 0;
    text[n] = 0;
    int count = 0;
    float near = 1e9f;
    for (char *line = text, *next; *line; line = next) {
        next = line;
        while (*next && *next != '\n') ++next;
        if (*next) *next++ = 0;
        char *fld[8] = { 0 };
        int k = 0;
        for (char *p = line; k < 8; ++k) { /* the first seven fields, then the first stream */
            fld[k] = p;
            while (*p && *p != '\t') ++p;
            if (!*p) {
                ++k;
                break;
            }
            *p++ = 0;
        }
        if (line[0] == '#' || k < 7 || tk_strcmp(fld[1], "FM") || count == RT_MAX) continue;
        rt_station *s = &rt->st[count];
        int freq;
        digits(fld[2], &freq);
        if (freq < LO || freq > HI) continue;
        memset(s, 0, sizeof *s);
        s->freq = (short)freq;
        int km10;
        digits(fld[3], &km10);
        s->km = km10 / 10.0f;
        s->db = (unsigned char)atoi(fld[4]);
        tk_snprintf(s->call, sizeof s->call, "%s", fld[0]);
        tk_snprintf(s->name, sizeof s->name, "%s", fld[5][0] ? fld[5] : fld[0]);
        tk_snprintf(s->city, sizeof s->city, "%s", fld[6]);
        if (k >= 8 && fld[7]) {
            char *eq = rt_chr(fld[7], '='), *tab = eq ? rt_chr(eq, '\t') : 0;
            if (tab) *tab = 0;
            if (eq) tk_snprintf(s->url, sizeof s->url, "%s", eq + 1);
        }
        if (s->km < near)
            near = s->km, tk_snprintf(rt->place, sizeof rt->place, "Near %s", s->city);
        ++count;
    }
    free(text);
    if (!count) return 0;
    rt->n = count;
    return 1;
}

static void title(void) {
    const char *t = rt->screen == LIST  ? rt->place
                    : rt->screen == ZIP ? "Change Location"
                                        : "Radio";
    if (!rt->title) return;
    widget_set_text_utf8(rt->title, t);
    bar_title_sync(rt->page);
}

static void screen(int s) {
    rt->screen = s;
    title();
    receive(); /* the loading screen holds the receiver silent */
    animate();
}

/* q2radiofetch for zip (0: the network's place) into LIST_FILE, the loading screen meanwhile. */
static void find(const char *zip) {
    unlink(LIST_FILE);
    if (rt_fetch_pid) return screen(LOADING); /* one is running: its list will do */
    int pid = fork();
    if (!pid) {
        child_oom_first();
        if (zip)
            execl(FETCH_BIN, FETCH_BIN, "--out", LIST_FILE, "--zip", zip, (char *)0);
        else
            execl(FETCH_BIN, FETCH_BIN, "--out", LIST_FILE, (char *)0);
        child_exit(127);
    }
    rt_fetch_pid = pid > 0 ? pid : 0;
    rt->fetch_at = time_now_ms();
    tk_snprintf(rt->err, sizeof rt->err, pid > 0 ? "" : "Couldn't start the search");
    screen(pid > 0 ? LOADING : ERROR);
}

/* A list arrived: the tuner, on the station the dial was on if it is still there, else the first
 * that streams. */
static void show_list(void) {
    if (!list_read()) return;
    int was = rt->screen;
    if (was == LOADING || was == ZIP || was == ERROR) {
        rt_station *s = at(rt->freq);
        if (!streams(s))
            for (int i = 0; i < rt->n && !streams(s); ++i) s = &rt->st[i];
        if (s) rt->freq = s->freq;
        rt->feed = -1;
        screen(TUNER);
        tune();
    } else
        animate();
}

/* ringnav_sleep, every loop pass: the fetch's end (reaped even with the page gone), its list, and
 * q2video's state; while the receiver runs, standby and auto power-off are held and the DAC kept
 * on. */
void tuner_poll(void) {
    if (rt_fetch_pid) {
        int status, done = waitpid(rt_fetch_pid, &status, 1) != 0; /* WNOHANG */
        if (done) rt_fetch_pid = 0;
        if (rt && rt->screen == LOADING && !access(LIST_FILE, 0))
            show_list(); /* the list, cached or fetched; a stale one is refreshed meanwhile */
        else if (rt && done && rt->screen == LOADING)
            tk_snprintf(rt->err, sizeof rt->err, "No internet connection"), screen(ERROR);
        else if (rt && done)
            show_list(); /* a refresh that ended after the list was on show: the newer one */
    }
    if (!rt) return;
    unsigned now = time_now_ms();
    int status, lost = wifi_offline(); /* the network monitor's link, as the status row's (status_row.c) */
    if (lost !=
        rt->no_wifi) { /* "No Wi-Fi" up or down; back, a stream not sounding starts again at once */
        if (!lost) rt_send("r");
        rt->no_wifi = lost;
        widget_invalidate_force(rt->sheet, 0);
    }
    if (rt->audio && waitpid(rt->audio, &status,
                             1)) /* the receiver gone (no output to open): tune starts it again */
        rt->audio = 0, rt->ended = now | 1, rt->feed = -1;
    if (!rt->audio) return;
    if (now - rt->polled < STATE_MS) return;
    rt->polled = now;
    char raw[600];
    if (state_read(TUNER_STATE, raw, sizeof raw)) {
        char t[256];
        field(raw, "state", rt->state, sizeof rt->state);
        field(raw, "title", t, sizeof t);
        if (tk_strcmp(t, rt->title_text) && rt->screen == TUNER)
            widget_invalidate_force(rt->sheet, 0);
        tk_snprintf(rt->title_text, sizeof rt->title_text, "%s", t);
    }
    if (rt->playing) reset_poweroptions_timer(1, 1, 0);
    if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
    if (rt->volume != headset_gain() + 1) {
        char v[2] = { 'v', (char)headset_gain() };
        rt->volume = headset_gain() + 1;
        dgram_send(&rt->sock, TUNER_SOCK, v, 2);
    }
}

/* ---- drawing ---- */

static void box(void *canvas, int x, int y, int w, int h, unsigned argb) {
    canvas_set_fill_color(canvas, argb);
    canvas_fill_rect(canvas, x, y - rt->y0, w, h);
}
static void round_box(void *canvas, int x, int y, int w, int h, unsigned rgb, unsigned r) {
    int rect[4] = { x, y - rt->y0, w, h };
    unsigned c = rgba(rgb, 255);
    if (canvas_fill_rounded_rect(canvas, rect, (void *)0, &c, r)) box(canvas, x, y, w, h, c);
}
/* text centred on screen row y (its line's centre, which for Inter is where its capitals' centre
 * is) */
static int rt_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int x, int y,
                   int w, int align) {
    return ui_text(canvas, s, medium, TYPE(px), rgb, 0, x, y - rt->y0, w, align);
}
/* ...placed by its baseline, as the prototype places the frequency: Inter's line centre sits
 * 0.3635 em above it */
static int rt_text_base(void *canvas, const char *s, int px, unsigned rgb, int x, int base) {
    return rt_text(canvas, s, 1, px, rgb, x, base - (px * 3635 + 5000) / 10000, 375, 2);
}

static void fit(char *out, unsigned cap, const char *s, void *canvas, int medium, int px, int max) {
    tk_snprintf(out, cap, "%s", s);
    if (ui_text_w(canvas, out, medium, TYPE(px)) <= max) return;
    unsigned n = strlen(out);
    while (n > 1) {
        out[--n] = 0;
        char t[128];
        tk_snprintf(t, sizeof t, "%s\xe2\x80\xa6", out);
        if (ui_text_w(canvas, t, medium, TYPE(px)) <= max) break;
    }
    unsigned m = strlen(out);
    if (m + 4 < cap) memcpy(out + m, "\xe2\x80\xa6", 4);
}

/* The scale under the needle at nd (100 kHz, fractional while gliding): a tick each 0.2 MHz (the
 * odd tenths, the US channels), longer on the whole MHz with labels on the even ones, a dot per
 * station 26 px above the baseline (filled: it streams; hollow: on air, no stream), both ends
 * fading out over FADE px, the needle in Favorite red. */
static void dial(void *canvas, float nd) {
    const float cx = 187.5f;
    for (int m = LO; m <= HI; m += 2) {
        float x = cx + (m - nd) * PX_MHZ / 10;
        if (x < -20 || x > 395) continue;
        int whole = (m + 1) % 10 == 0, n = (m + 1) / 10;
        box(canvas, (int)(x - 0.75f + 0.5f), DIAL_Y - (whole ? 14 : 7), 2, whole ? 14 : 7,
            rgba(whole ? RGB_SECONDARY : RGB_TERTIARY, 255));
        if (whole && n % 2 == 0) {
            char l[8];
            tk_snprintf(l, sizeof l, "%d", n);
            rt_text(canvas, l, 0, 13, RGB_TERTIARY, (int)(x + 3.1f), DIAL_Y + 14, 40, 1);
        }
    }
    for (int i = 0; i < rt->n; ++i) {
        float x = cx + (rt->st[i].freq - nd) * PX_MHZ / 10;
        if (x < -10 || x > 385) continue;
        int dx = (int)(x - 3.5f + 0.5f);
        round_box(canvas, dx, DIAL_Y - 30, 7, 7, streams(&rt->st[i]) ? RGB_PRIMARY : RGB_TERTIARY,
                  3);
        if (!streams(&rt->st[i])) round_box(canvas, dx + 2, DIAL_Y - 28, 3, 3, RGB_CANVAS, 1);
    }
    for (int k = 0; k < FADE; ++k) { /* the ends into the black, as the house marquee's */
        unsigned a = (unsigned)(255 * (FADE - k) / FADE);
        box(canvas, k, DIAL_Y - 40, 1, 60, rgba(RGB_CANVAS, a));
        box(canvas, 374 - k, DIAL_Y - 40, 1, 60, rgba(RGB_CANVAS, a));
    }
    round_box(canvas, 186, DIAL_Y - 40, 3, 44, RGB_FAVORITE, 1);
}

/* A word of a title in all capitals (as stations' automation sends them) in title case: a word with
 * a dot, slash, digit or apostrophe in it keeps its capitals (R.I.P., AC/DC), and a small word
 * after the first is lower case (of, the, and). */
static void cased(char *s) {
    static const char *const small[] = { "a",  "an", "and", "as",  "at", "by", "for", "in",
                                         "of", "on", "or",  "the", "to", "vs", "with" };
    int lower = 0;
    for (char *c = s; *c; ++c) lower |= *c >= 'a' && *c <= 'z';
    if (lower) return; /* the station's own casing */
    for (char *w = s, first = 1; *w;) {
        char *e = w;
        int keep = 0;
        while (*e && *e != ' ')
            keep |= *e == '.' || *e == '/' || *e == '\'' || (*e >= '0' && *e <= '9'), ++e;
        if (!keep) {
            for (char *c = w + 1; c < e; ++c)
                if (*c >= 'A' && *c <= 'Z' && c[-1] != '-') *c += 32;
            for (unsigned k = 0; !first && k < sizeof small / sizeof *small; ++k)
                if ((unsigned)(e - w) == strlen(small[k]) && !strncasecmp(w, small[k], e - w))
                    *w += 32 * (*w <= 'Z');
        }
        first = 0;
        w = *e ? e + 1 : e;
    }
}

/* A StreamTitle as the tuner shows it: "Artist - Title" as sent, or iHeart's key="value" pairs
 * (title="R.I.P.",artist="MOTIONLESS IN WHITE",url="...") as "Artist - Title", or a spot's text=;
 * all capitals made title case (cased). Shared with test/fw. */
void tuner_title(const char *in, char *out, unsigned cap) {
    char artist[128] = "", song[128] = "", spot[128] = "";
    int pairs = 0;
    for (const char *p = in; (p = strstr(p, "=\""));) {
        const char *k = p;
        while (k > in && ((k[-1] | 32) >= 'a' && (k[-1] | 32) <= 'z')) --k;
        const char *v = p + 2, *e = rt_chr(v, '"');
        if (!e) break;
        char *to = !strncmp(k, "artist=", 7)  ? artist
                   : !strncmp(k, "title=", 6) ? song
                   : !strncmp(k, "text=", 5)  ? spot
                                              : 0;
        if (to) tk_snprintf(to, 128, "%.*s", (int)(e - v), v), cased(to);
        pairs = 1, p = e + 1;
    }
    if (!pairs) {
        tk_snprintf(out, cap, "%s", in);
        cased(out);
    } else if (artist[0] && song[0])
        tk_snprintf(out, cap, "%s - %s", artist, song);
    else
        tk_snprintf(out, cap, "%s", song[0] ? song : artist[0] ? artist : spot);
}

static void tuner_screen(void *canvas) {
    rt_station *s = at(rt->freq);
    int ok = streams(s), dim = s && !ok;
    char num[8];
    tk_snprintf(num, sizeof num, "%d.%d", rt->freq / 10, rt->freq % 10);
    int nw = ui_text_w(canvas, num, 1, TYPE(64)), fw = ui_text_w(canvas, "FM", 1, TYPE(17));
    int x0 = (375 - nw - 6 - fw) / 2;
    rt_text_base(canvas, num, 64, dim ? RGB_TERTIARY : RGB_PRIMARY, x0, 116);
    rt_text_base(canvas, "FM", 17, RGB_SECONDARY, x0 + nw + 6, 116);
    char line[256];
    if (!s) {
        rt_text(canvas, "No station", 1, 20, RGB_PRIMARY, 187, 148, 375, 1);
        rt_text(canvas, "Keep turning, or press Next to seek", 0, 15, RGB_SECONDARY, 187, 174, 375,
                1);
    } else {
        fit(line, sizeof line, s->name, canvas, 1, 20, 375 - 60);
        rt_text(canvas, line, 1, 20, dim ? RGB_TERTIARY : RGB_PRIMARY, 187, 148, 375, 1);
        char two[64];
        tk_snprintf(two, sizeof two, "%s \xc2\xb7 %s", s->call, s->city);
        char *comma = strrchr(two, ','); /* the city without its state, as the prototype */
        if (comma) *comma = 0;
        rt_text(canvas, two, 0, 15, RGB_SECONDARY, 187, 174, 375, 1);
        const char *l3 = !ok                       ? "On air here, not available to stream"
                         : !rt->playing            ? "Paused"
                         : index_of(s) == rt->feed ? rt->title_text
                                                   : "";
        char t[256];
        tuner_title(l3, t, sizeof t);
        if (!t[0] && ok && rt->playing && index_of(s) == rt->feed &&
            tk_strcmp(rt->state, "playing"))
            tk_snprintf(t, sizeof t, "Tuning\xe2\x80\xa6");
        fit(line, sizeof line, t, canvas, 0, 15, 375 - 60);
        if (line[0]) rt_text(canvas, line, 0, 15, RGB_TERTIARY, 187, 196, 375, 1);
    }
    dial(canvas, needle());
}

/* The now-playing mark: three 3 px bars 5 px apart, up to 14 px tall, out of step, their foot at
 * y + 7 (the prototype's musicBars). */
static void bars(void *canvas, int x, int y, unsigned rgb) {
    static const float ph[3] = { 0, 1.7f, 0.9f }, sp[3] = { 5.1f, 6.3f, 4.4f };
    float t = time_now_ms() / 1000.0f;
    for (int i = 0; i < 3; ++i) {
        float h = 4 + 10 * (0.5f + 0.5f * (float)sin(t * sp[i] + ph[i])) *
                          (0.6f + 0.4f * (float)sin(t * 1.3f + i));
        round_box(canvas, x - 7 + i * 5, (int)(y + 7 - h + 0.5f), 3, (int)(h + 0.5f), rgb, 1);
    }
}

static int items(void) { return rt->n + 2; } /* the stations, Use My Location, Enter ZIP Code */

static void list_screen(void *canvas) {
    int first = (int)(rt->target / ROW);
    if (rt->sel < first) rt->target = (float)(rt->sel * ROW);
    if (rt->sel > first + LIST_ROWS - 1) rt->target = (float)((rt->sel - LIST_ROWS + 1) * ROW);
    float most = (float)((items() > LIST_ROWS ? items() - LIST_ROWS : 0) * ROW);
    if (rt->target > most) rt->target = most;
    if (rt->target < 0) rt->target = 0;
    for (int i = 0; i < items(); ++i) {
        int y = LIST_TOP + i * ROW - (int)(rt->scroll + 0.5f);
        if (y < 10 || y > 320) continue;
        int on = i == rt->sel;
        if (on) sel_bar_flat(canvas, 0, y - rt->y0, 375, ROW);
        if (i >= rt->n) {
            rt_text(canvas, i == rt->n ? "Use My Location" : "Enter ZIP Code\xe2\x80\xa6", 0, 20,
                    RGB_PRIMARY, 26, y + 16, 300, 2);
            rt_text(canvas, "\xe2\x80\xba", 0, 26, on ? RGB_PRIMARY : RGB_CHEVRON, 342, y + 15, 20,
                    1);
            continue;
        }
        rt_station *s = &rt->st[i];
        char f[8], name[96];
        tk_snprintf(f, sizeof f, "%d.%d", s->freq / 10, s->freq % 10);
        int rw = ui_text_w(canvas, f, 0, TYPE(20));
        int playing = streams(s) && rt->playing && index_of(s) == rt->feed && s->freq == rt->freq;
        fit(name, sizeof name, s->name, canvas, 0, 20,
            375 - 28 - 26 - rw - 14 - (playing ? 26 : 0));
        rt_text(canvas, name, 0, 20, !streams(s) && !on ? RGB_TERTIARY : RGB_PRIMARY, 26, y + 16,
                300, 2);
        rt_text(canvas, f, 0, 20, on ? RGB_PRIMARY : RGB_SECONDARY, 375 - 28, y + 16, 120, 3);
        if (playing) bars(canvas, 375 - 28 - rw - 20, y + 16, on ? RGB_PRIMARY : RGB_ACCENT_TEXT);
    }
    int more = items() - LIST_ROWS;
    for (int k = 0; k < 34 && rt->scroll > 1;
         ++k) /* the fades where the list goes on past an edge */
        box(canvas, 0, 40 + k, 375, 1, rgba(RGB_CANVAS, (unsigned)(255 * (34 - k) / 34)));
    for (int k = 0; k < 44 && more > 0 && rt->scroll < more * ROW - 1; ++k)
        box(canvas, 0, 266 + k, 375, 1, rgba(RGB_CANVAS, (unsigned)(255 * k / 44)));
    unsigned age = time_now_ms() - rt->shown_at;
    if (more > 0 && age < UI_SHOW_MS) { /* the scroll indicator while the wheel turns */
        unsigned a = ui_fade_alpha(rt->shown_at);
        int len = 68 * LIST_ROWS / items() > 8 ? 68 * LIST_ROWS / items() : 8;
        int pos = 136 + (int)((68 - len) * rt->scroll / (more * ROW));
        round_box(canvas, 364, 136, 4, 68, RGB_OFF, 2);
        int rect[4] = { 364, pos - rt->y0, 4, len };
        unsigned c = rgba(0xe5e5e5, a);
        canvas_fill_rounded_rect(canvas, rect, (void *)0, &c, 2);
    }
}

static void zip_screen(void *canvas) {
    rt_text(canvas, "Enter ZIP Code", 1, 20, RGB_PRIMARY, 187, 92, 375, 1);
    rt_text(canvas, "Turn to choose, Centre for the next digit", 0, 15, RGB_SECONDARY, 187, 118,
            375, 1);
    for (int i = 0; i < 5; ++i) {
        int x = 187 - 2 * 52 + i * 52;
        if (i == rt->zi)
            sel_bar_flat(canvas, x - 21 - theme()->sel_inset, 150 - rt->y0,
                         42 + 2 * theme()->sel_inset, 56);
        else
            round_box(canvas, x - 21, 150, 42, 56, RGB_RAISED, 10);
        char d[2] = { (char)('0' + rt->zip[i]), 0 };
        if (i <= rt->zi) rt_text(canvas, d, 1, 30, RGB_PRIMARY, x, 179, 42, 1);
    }
    rt_text(canvas, "Menu: back", 0, 13, RGB_TERTIARY, 187, 250, 375, 1);
}

static void loading_screen(void *canvas) {
    float t = time_now_ms() / 1000.0f;
    for (int i = 0; i < 8; ++i) { /* the spinner: eight spokes, the lit one going round */
        float a = i / 8.0f * 6.2831853f, k = i / 8.0f - t;
        k -= (float)(int)k;
        if (k < 0) k += 1;
        unsigned alpha = (unsigned)(255 * (0.15f + 0.85f * (1 - k)));
        for (int r = 9; r <= 16; ++r)
            box(canvas, (int)(187.5f + (float)cos(a) * r), (int)(130 + (float)sin(a) * r), 3, 3,
                rgba(RGB_PRIMARY, alpha));
    }
    rt_text(canvas,
            rt->zip_text[0] ? "Looking up your ZIP Code\xe2\x80\xa6"
                            : "Finding stations near you\xe2\x80\xa6",
            1, 17, RGB_PRIMARY, 187, 182, 375, 1);
    char s[16];
    unsigned ms = time_now_ms() - rt->fetch_at;
    tk_snprintf(s, sizeof s, "%u.%u s", ms / 1000, ms / 100 % 10);
    rt_text(canvas, s, 0, 15, RGB_TERTIARY, 187, 208, 375, 1);
}

static void error_screen(void *canvas) {
    rt_text(canvas, "Couldn\xe2\x80\x99t find stations", 1, 20, RGB_PRIMARY, 187, 140, 375, 1);
    rt_text(canvas, rt->err, 0, 15, RGB_SECONDARY, 187, 168, 375, 1);
    rt_text(canvas, "Centre: try again \xc2\xb7 Menu: enter a ZIP", 0, 15, RGB_TERTIARY, 187, 200,
            375, 1);
}

/* ringnav_paint (nav/row_paint.c): the page's sheet. */
void tuner_paint(void *w, void *canvas) {
    if (!rt || w != rt->sheet || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    if (rt->screen == TUNER)
        tuner_screen(canvas);
    else if (rt->screen == LIST)
        list_screen(canvas);
    else if (rt->screen == ZIP)
        zip_screen(canvas);
    else if (rt->screen == LOADING)
        loading_screen(canvas);
    else
        error_screen(canvas);
    if (rt->no_wifi) toast_stay(canvas, rt->y0, "No Wi-Fi"); /* for as long as the link is down */
    stock_canvas_set_fill_color(canvas, fill);
}

/* A repaint while something moves: the needle gliding (with the receiver told), the list sliding,
 * its indicator fading or its music bars playing, the spinner; then it stops. */
static int frame(const void *unused) {
    if (!rt) return 0;
    int moving = 0;
    if (rt->screen == TUNER && rt->glide_at) moving = 1, needle(), receive();
    if (rt->screen == LIST) {
        float d = rt->target - rt->scroll;
        if (d > 0.3f || d < -0.3f)
            rt->scroll += d * 0.35f, moving = 1;
        else
            rt->scroll = rt->target;
        rt_station *f = rt->feed >= 0 ? &rt->st[rt->feed] : 0;
        moving |= time_now_ms() - rt->shown_at < UI_SHOW_MS + 100 ||
                  (f && rt->playing && f->freq == rt->freq);
    }
    moving |= rt->screen == LOADING;
    widget_invalidate_force(rt->sheet, 0);
    if (!moving) rt->anim = 0;
    return moving ? 8 : 0; /* RET_REPEAT */
}

/* ---- input ---- */

/* Next or Previous: the next station up or down that streams, round the ends. */
static void seek(int d) {
    int best = -1;
    for (int i = 0; i < rt->n; ++i) {
        rt_station *s = &rt->st[i];
        if (!streams(s)) continue;
        if (d > 0 ? s->freq > rt->freq && (best < 0 || s->freq < rt->st[best].freq)
                  : s->freq < rt->freq && (best < 0 || s->freq > rt->st[best].freq))
            best = i;
    }
    for (int i = 0, wrap = best < 0; wrap && i < rt->n; ++i) /* round the end */
        if (streams(&rt->st[i]) && (best < 0 || (d > 0 ? rt->st[i].freq < rt->st[best].freq
                                                       : rt->st[i].freq > rt->st[best].freq)))
            best = i;
    if (best >= 0) rt->freq = rt->st[best].freq, tune();
}

static void zip_find(void) {
    char z[6];
    for (int i = 0; i < 5; ++i) z[i] = (char)('0' + rt->zip[i]);
    z[5] = 0;
    tk_snprintf(rt->zip_text, sizeof rt->zip_text, "%s", z);
    int fd = open(ZIP_FILE, 0x301 /* O_WRONLY | O_CREAT | O_TRUNC */, 0644);
    if (fd >= 0) write(fd, z, 5), close(fd);
    find(z);
}

static void act(void) { /* Centre on the list's row */
    if (rt->sel < rt->n) {
        rt->freq = rt->st[rt->sel].freq;
        screen(TUNER);
        tune();
    } else if (rt->sel == rt->n) {
        unlink(ZIP_FILE), rt->zip_text[0] = 0;
        find(0);
    } else {
        int z[5] = { 6, 0, 6, 0, 1 };
        memcpy(rt->zip, z, sizeof z), rt->zi = 0;
        screen(ZIP);
    }
}

/* ringnav (nav/input.c): the page's keys, before stock's; 0 for those it leaves to stock (Return on
 * the tuner, which leaves the page through keyup). */
int tuner_key(void *top, unsigned key) {
    if (!rt || top != rt->page) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0, s = rt->screen;
    if (key == KEY_PLAY && (s == TUNER || s == LIST)) {
        rt->playing = !rt->playing;
        tune();
        return 1;
    }
    if (s == TUNER) {
        if (key == KEY_RETURN) return 0;
        if (wheel) {
            int f = rt->freq + 2 * wheel;
            rt->freq = f < LO ? LO : f > HI ? HI : f;
            tune();
        } else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
            seek(key == KEY_FWD_BTN ? 1 : -1);
        else if (key == KEY_CENTER) {
            rt->sel = index_of(at(rt->freq)) > 0 ? index_of(at(rt->freq)) : 0;
            rt->scroll = rt->target = (float)((rt->sel > 3 ? rt->sel - 3 : 0) * ROW);
            screen(LIST);
        }
    } else if (s == LIST) {
        if (key == KEY_RETURN)
            screen(TUNER), tune();
        else if (wheel) {
            int n = rt->sel + wheel;
            rt->sel = n < 0 ? 0 : n >= items() ? items() - 1 : n;
            rt->shown_at = time_now_ms();
            animate();
        } else if (key == KEY_CENTER)
            act();
    } else if (s == ZIP) {
        if (key == KEY_RETURN || key == KEY_BACK_BTN) {
            if (rt->zi)
                --rt->zi, animate();
            else
                screen(LIST);
        } else if (wheel)
            rt->zip[rt->zi] = (rt->zip[rt->zi] + wheel + 10) % 10, animate();
        else if (key == KEY_CENTER || key == KEY_FWD_BTN) {
            if (rt->zi < 4)
                ++rt->zi, animate();
            else
                zip_find();
        }
    } else if (s == ERROR) {
        if (key == KEY_CENTER)
            find(rt->zip_text[0] ? rt->zip_text : 0);
        else if (key == KEY_RETURN) {
            int z[5] = { 6, 0, 6, 0, 1 };
            memcpy(rt->zip, z, sizeof z), rt->zi = 0;
            screen(ZIP);
        }
    } else if (key == KEY_RETURN)
        return 0; /* loading: Return leaves */
    return 1;
}

/* A finger: on the tuner's dial (y 225 down) a tap tunes to the frequency under it, snapping to a
 * station within 0.15 MHz, and a drag scrubs, the scale following it; on the list a tap acts on the
 * row. */
static int touch(void *ctx, void *event) {
    if (!rt) return 0;
    int type = I(event, EVENT_TYPE), xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(rt->sheet, xy);
    int x = xy[0], y = xy[1] + rt->y0;
    if (type == EVT_POINTER_DOWN) {
        rt->touch = (rt->screen == TUNER && y >= 225) || rt->screen == LIST;
        rt->tx = x, rt->tf = rt->freq, rt->moved = 0;
        return 0;
    }
    if (!rt->touch) return 0;
    if (type == EVT_POINTER_MOVE_BEFORE && rt->screen == TUNER) {
        if (!rt->moved && x - rt->tx < 4 && rt->tx - x < 4) return 0;
        rt->moved = 1;
        int f = rt->tf - (x - rt->tx) * 10 / PX_MHZ; /* the scale moves under a fixed needle */
        f = f - ((f - LO) % 2 + 2) % 2;              /* onto a channel */
        f = f < LO ? LO : f > HI ? HI : f;
        if (f != rt->freq) rt->freq = f, tune();
        return 11; /* RET_STOP */
    }
    if (type == EVT_POINTER_UP_BEFORE) {
        rt->touch = 0;
        if (rt->screen == TUNER && !rt->moved) {
            int f = rt->freq + (x - 187) * 10 / PX_MHZ;
            rt_station *best = 0;
            for (int i = 0; i < rt->n; ++i) {
                int d = rt->st[i].freq - f;
                if (d < 0) d = -d;
                if (d <= 1 && (!best || d < (best->freq > f ? best->freq - f : f - best->freq)))
                    best = &rt->st[i];
            }
            f = best ? best->freq : f - ((f - LO) % 2 + 2) % 2;
            rt->freq = f < LO ? LO : f > HI ? HI : f;
            tune();
        } else if (rt->screen == LIST && !rt->moved) {
            int i = (y - LIST_TOP + (int)rt->scroll) / ROW;
            if (y >= 40 && i >= 0 && i < items()) rt->sel = i, act();
        }
        return 11;
    }
    return 0;
}

static int leave(const void *unused) {
    rt->leave = 0;
    navigator_back();
    return 0;
}

static int keyup(void *ctx, void *event) { return return_key(event, &rt->leave, leave); }

/* The receiver asked to quit, and killed if it does not go within QUIT_MS. */
static void audio_halt(void) {
    if (!rt->audio) return;
    rt_send("q");
    int status, done;
    unsigned start = time_now_ms();
    while (!(done = waitpid(rt->audio, &status, 1)) && time_now_ms() - start < QUIT_MS)
        sleep_ms(20);
    if (!done) {
        char kill[24]; /* demo imports no kill */
        tk_snprintf(kill, sizeof kill, "kill -9 %d", rt->audio);
        system(kill);
        waitpid(rt->audio, &status, 0);
    }
    rt->audio = 0;
}

static int closed(void *ctx, void *event) {
    stop_timer(&rt->anim), stop_timer(&rt->land), stop_timer(&rt->warm), stop_timer(&rt->leave);
    audio_halt();
    if (rt->sock > 0) close(rt->sock);
    free(rt);
    rt = 0;
    return 0;
}

/* Home's Radio row: the page, the receiver started (whatever else held the output stopped),
 * and the stations for the chosen ZIP or the network's place looked up. */
int tuner_open(void *ctx, void *event) {
    if (rt || !(rt = calloc(1, sizeof *rt))) return 0;
    void *page = rt->page = page_open("tuner_page", closed, keyup);
    if (!page) {
        free(rt);
        rt = 0;
        return 0;
    }
    rt->y0 = bar_foot(page);
    rt->sheet = widget_factory_create_widget(widget_factory(), "view", page, 0, rt->y0, 375,
                                             page_room(page));
    rt->title = page_title(page, "Radio", 1);
    widget_on(rt->sheet, EVT_POINTER_DOWN, touch, 0);
    widget_on(page, EVT_POINTER_MOVE_BEFORE, touch, 0);
    widget_on(page, EVT_POINTER_UP_BEFORE, touch, 0);
    rt->freq = 931, rt->playing = 1, rt->feed = -1, rt->sock = -1;
    headset_manual();
    spot_yield(); /* librespot, Internet Radio and the player hold the output too */
    radio_stop();
    smart_wake(); /* Smart Wi-Fi's radio back now; tuner_page holds it on (smart_wifi.c
                     net_page_open) */
    rt->audio = video_start("-", 2);
    rt->volume = headset_gain() + 1;
    char z[8] = "";
    void *f = fopen(ZIP_FILE, "rb");
    if (f) z[fread(z, 1, 5, f)] = 0, fclose(f);
    tk_snprintf(rt->zip_text, sizeof rt->zip_text, "%s", strlen(z) == 5 ? z : "");
    find(rt->zip_text[0] ? rt->zip_text : 0);
    return 0;
}
