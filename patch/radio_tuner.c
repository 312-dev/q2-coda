/* Radio (docs/internals.md#radio-home, docs/oled-design.md#radio): Home's row under
 * the Library. The stations that reach the place, from q2radiofetch (radiofetch.c), at their real
 * frequencies on an FM dial under a fixed needle; the wheel tunes finely (0.02 MHz a step, 0.06
 * turned fast), each station a detent, every step a click, the needle gliding after the dial; and
 * the sound is q2video -t's FM receiver (video.c, fmrx.c): hiss between stations, a station's
 * stream swelling out of it as the needle closes on it. A station's name and song wait for its
 * stream, as RDS does. The approved prototype (~/q2-emu/design/radio-tuner/index.html) is the
 * spec, screen for screen. Centre tapped on the tuner shows the volume in the dial's place, held
 * opens the stations list (~/q2-emu/design/radio-tuner/centre-volume/sheet.png, approved
 * 2026-11-20).
 *
 * Screens: loading, the tuner, the stations list (Centre held), the ZIP entry (the list's last
 * row) and the error. The state is on the heap while the page is open; the scratch holds its
 * pointer and the fetch's pid, reaped after the page has gone. */
#include "navigation.h"

#define FETCH_BIN "/usr/bin/q2radiofetch"
#define LIST_FILE "/tmp/q2tuner.list" /* q2radiofetch --out: the list, whole or not at all */
#define ZIP_FILE                                                                                   \
    "/mnt/mmc/Radio/.local/zip"        /* the ZIP the user chose; none: the network's place        \
                                        */
#define TUNER_SOCK "/tmp/q2tuner.sock" /* video.c's */
#define FREQ_FILE "/mnt/mmc/Radio/.local/freq" /* the station last landed on, 10 kHz as text */
#define TUNER_STATE "/tmp/q2tuner.state"
#define RT_MAX 64 /* FM stations a list holds */
#define RT_URL 300
#define LO 8790 /* the dial's ends, in 10 kHz, the unit of every frequency here */
#define HI 10790
#define PX_MHZ 62         /* the dial's scale */
#define DIAL_Y 270        /* its baseline, screen y */
#define RT_GLIDE_MS 90.0f /* the needle's time constant toward the dial's step */
/* Fine tuning (the prototype's fineTurn): a step FINE, FAST when steps come under FAST_MS apart. A
 * station is a detent: a step that would reach within CAPTURE of it, or pass it, lands on it, and
 * the next HOLD steps the same way only click in place. HEAR: a station within it (not at it) is
 * the one the receiver hears, off centre. A tap lands on a station within TAP_SNAP of it. */
#define FINE 2
#define FAST 6
#define FAST_MS 35
#define CAPTURE 4
#define HOLD 3
#define HEAR 10
#define TAP_SNAP 15
/* The touch wheel's raw travel, 200 units a turn, makes a step every 12 degrees (the prototype's
 * drag): STEP_THIRDS thirds of a unit, 30 steps a turn, where a tick (7 a turn at 50% Wheel
 * Sensitivity) would be 51 degrees. */
#define STEP_THIRDS 20
/* The words (the prototype's reveal): up once the station's stream is sounding and its title has
 * come, or NO_TITLE_MS of sound has passed with none, fading in over REVEAL_MS. A line wider than
 * WORDS_W is the house marquee: MARQ_HOLD_MS still, then MARQ_PX px a second, a copy MARQ_GAP
 * behind, its ends faded over MARQ_FADE px, repainted every MARQ_MS. */
#define NO_TITLE_MS 4000
#define REVEAL_MS 400
#define WORDS_W 315
#define MARQ_HOLD_MS 1500
#define MARQ_PX 30
#define MARQ_GAP 40
#define MARQ_FADE 16
#define MARQ_MS 33
#define LAND_MS 350  /* the needle at rest this long before a stream starts */
#define WARM_MS 4000 /* a stream left playing this long after the dial leaves its station */
#define FRAME_MS 16  /* a repaint while something moves */
#define STATE_MS 500 /* q2video's state read this often */
#define QUIT_MS 1500 /* q2video's own exit, before it is killed */
#define ROW 32       /* the list's rows, from LIST_TOP */
#define LIST_TOP 58
#define LIST_ROWS 7
#define FADE 48 /* the dial's end fades */

enum { LOADING, TUNER, LIST, ZIP, ERROR };

typedef struct {
    short freq; /* 10 kHz */
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
    int det_f, det_left,
        det_d;        /* the detent: the station held on (0 none), steps left, which way */
    unsigned tick_at; /* the dial's last step */
    int travel;       /* the wheel's raw travel not yet a step, thirds of a unit */
    int shown;        /* the station whose words are up, its index + 1; 0 none */
    int raw;          /* the tuner has the wheel raw (wheel_raw) */
    unsigned words_at, locked_at, marq; /* locked_at: when the feed's stream first sounded */
    unsigned mq_at[2], mq_key[2];       /* the marquee lines' clocks, and their texts' hashes */
    int live; /* the last paint moved: the words fading in, a line scrolling */
    int vol;  /* the volume band shows in the dial's place (Centre tapped), the wheel its steps */
    unsigned vol_timer, hold; /* the band's SCRUB_MS since the last turn or press; Centre's hold */
    int held;                 /* that hold went off: its release is taken */
    unsigned tap_at;          /* a tap's release, and the next tap within DOUBLE_CLICK_MS ... */
    int tap_armed;            /* ... is its double press, the wheel not turned between */
    int ending;               /* tuner_stop shutting the page: closed ends Radio too */
} *rt __attribute__((section(".scratch")));
static int rt_fetch_pid __attribute__((section(".scratch")));

static float needle(void) {
    if (!rt->glide_at) return (float)rt->freq;
    float d = (rt->from - rt->freq) *
              (float)pow(2.718281828, -(double)(time_now_ms() - rt->glide_at) / RT_GLIDE_MS);
    if (d < 0.2f && d > -0.2f) return rt->glide_at = 0, (float)rt->freq; /* landed: within 2 kHz */
    return rt->freq + d;
}

static char *rt_chr(const char *s, int c) { /* demo imports no strchr */
    for (; *s; ++s)
        if (*s == c) return (char *)s;
    return 0;
}

/* The station the receiver hears at freq: the nearest within HEAR (off centre it sounds distorted,
 * as a real set). */
static rt_station *at(float freq) {
    rt_station *best = 0;
    float bd = HEAR;
    for (int i = 0; i < rt->n; ++i) {
        float d = rt->st[i].freq - freq;
        if (d < 0) d = -d;
        if (d < bd) bd = d, best = &rt->st[i];
    }
    return best;
}
static int on_station(int freq) {
    for (int i = 0; i < rt->n; ++i)
        if (rt->st[i].freq == freq) return 1;
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
    rt_station *here = at((float)rt->freq), *c = 0;
    float level = -40, fade = 0;
    int id = 0;
    if (here && !streams(here))
        c = here, level = 9, fade = 0.7f, id = -here->freq;
    else if (rt->feed >= 0)
        c = &rt->st[rt->feed], level = c->db, fade = flutter(c->km), id = c->freq;
    char p[96]; /* ints: dB, kHz, the fade in thousandths, the gain, the station (video.c tuner_key)
                 */
    tk_snprintf(p, sizeof p, "p%d %d %d %d %d", (int)level,
                c ? (int)((needle() - c->freq) * 10) : 0, (int)(fade * 1000),
                rt->playing && rt->screen != LOADING ? 1 : 0, id);
    rt_send(p);
}

/* The station whose stream the receiver should have: the one on the dial, or on an empty channel
 * the stronger of its neighbours 0.2 MHz either side (on the 0.1 MHz grid) at 70 dB or more, which
 * splatters in. */
static int source(void) {
    rt_station *here = at((float)rt->freq);
    if (here) return streams(here) ? index_of(here) : -1;
    rt_station *a = at((float)((rt->freq - 20 + 5) / 10 * 10)),
               *b = at((float)((rt->freq + 20 + 5) / 10 * 10)), *best = 0;
    if (streams(a) && a->db >= 70) best = a;
    if (streams(b) && b->db >= 70 && (!best || b->db > best->db || index_of(b) == rt->feed))
        best = b;
    if (best && index_of(a) == rt->feed && streams(a) && a->db >= 70) best = a;
    return index_of(best);
}

/* The dial's frequency kept on the card, where a page opened with no receiver starts (tuner_open),
 * as the Library resumes where it was. */
static void freq_save(void) {
    char f[8];
    int n = tk_snprintf(f, sizeof f, "%d", rt->freq);
    void *out = fopen(FREQ_FILE, "wb");
    if (out) fwrite(f, 1, (unsigned)n, out), fclose(out);
}

static int land(const void *unused) {
    rt->land = 0;
    freq_save(); /* the dial at rest: where Radio starts next time */
    int s = source();
    if (s >= 0 && s != rt->feed && rt->playing) {
        char u[RT_URL + 2];
        tk_snprintf(u, sizeof u, "u%s", rt->st[s].url);
        rt_send(u);
        rt->feed = s;
        rt->title_text[0] = rt->state[0] = 0, rt->locked_at = 0;
        receive();
    }
    return 0;
}

static int cool(const void *unused) { /* the dial left the loaded stream's station WARM_MS ago */
    rt->warm = 0;
    if (rt->feed >= 0 && source() != rt->feed)
        rt_send("x"), rt->feed = -1, rt->title_text[0] = 0, rt->locked_at = 0, receive();
    return 0;
}

static int frame(const void *unused);

/* A repaint of the page, while it is open: Radio plays on with it shut (closed). */
static void redraw(void) {
    if (rt->sheet) widget_invalidate_force(rt->sheet, 0);
}
static void animate(void) {
    if (!rt->page) return;
    if (!rt->anim) rt->anim = timer_add(frame, 0, FRAME_MS);
    redraw();
}

/* The dial moved to rt->freq (or play or pause): the needle glides there, the stream it lands on
 * starts LAND_MS later, a stream the dial left stays warm WARM_MS. */
static void tune(void) {
    if (!rt->audio && time_now_ms() - rt->ended > 3000) /* gone: started again, at most every 3 s */
        rt->audio = video_start("-", 2), rt->volume = 0;
    float n = needle();
    if ((float)rt->freq != n) rt->from = n, rt->glide_at = time_now_ms();
    int s = rt->playing ? source() : -1;
    if (!rt->playing && rt->feed >= 0) rt_send("x"), rt->feed = -1, rt->locked_at = 0;
    rt_station *here = at((float)rt->freq);
    if (rt->shown && (!here || index_of(here) + 1 != rt->shown))
        rt->shown = 0; /* the dial left it */
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

/* The dial's click, its loudness in tenths (tuner_core.c tuner_click_start): 6 a held detent, 10 a
 * step, 14 landing on a station, 5 a scrub between stations. */
static void click(int tenths) {
    char k[8];
    tk_snprintf(k, sizeof k, "k%d", tenths);
    rt_send(k);
}

/* n steps of the dial (the prototype's fineTurn), FAST ones when fast: a station within CAPTURE
 * of where a step would go, or passed by it, is landed on, and holds the next HOLD steps the same
 * way, each a click in place; sitting on one (landed by seek, the list or a tap) holds alike.
 * piezo: the steps came from the raw wheel, which posts no key and so no Key Tone click: each step
 * that moves the dial asks the buzzer for one (input.c ringnav_piezo, under the Clicker), and one
 * asked while the last still sounds is dropped, so a fast spin is a train at the buzzer's own pace.
 * A tick (a key) had stock's click on its press already. */
static void fine_turn(int n, int fast, int piezo) {
    int d = n > 0 ? 1 : -1, step = fast ? FAST : FINE;
    for (int k = 0; k < n * d; ++k) {
        if ((rt->det_f != rt->freq || rt->det_d != d) && on_station(rt->freq))
            rt->det_f = rt->freq, rt->det_left = HOLD, rt->det_d = d;
        if (rt->det_f && rt->det_d == d && rt->det_left > 0) {
            --rt->det_left;
            click(6);
            continue;
        }
        rt->det_f = 0;
        int f = rt->freq + d * step;
        f = f < LO ? LO : f > HI ? HI : f;
        rt_station *hit = 0;
        for (int i = 0; i < rt->n; ++i) {
            int sf = rt->st[i].freq;
            if ((sf - rt->freq) * d > 0 && (sf - f) * d <= CAPTURE &&
                (!hit || (sf - hit->freq) * d < 0))
                hit = &rt->st[i];
        }
        if (hit) f = hit->freq, rt->det_f = f, rt->det_left = HOLD, rt->det_d = d;
        click(hit ? 14 : 10);
        if (piezo && f != rt->freq) ringnav_piezo();
        rt->freq = f;
    }
    tune();
}

/* The touch wheel, raw while the tuner screen is the top window's and lit (wheel_raw, playback.h):
 * its travel as steps, STEP_THIRDS thirds of a unit each, clockwise up; fast when they came under
 * FAST_MS apart. No key event comes, so travel holds the screen on itself, as Now Playing's lyrics
 * do. Given back to ticks anywhere else, and with the screen off, so a turn wakes it as a tick
 * does. */
static void wheel(unsigned now) {
    int raw = rt->page && rt->screen == TUNER && !rt->vol && g_backlight_status &&
              window_manager_get_top_window(window_manager()) == rt->page;
    if (raw != rt->raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        rt->raw = raw, rt->travel = 0;
        if (raw || wheel_raw == WHEEL_RAW) wheel_raw = raw ? WHEEL_RAW : WHEEL_TICKS;
    }
    if (!raw) return;
    int travel = __sync_lock_test_and_set(&wheel_travel, 0);
    if (travel) reset_poweroptions_timer(1, 1, 1);
    rt->travel -= 3 * travel; /* the travel rises anticlockwise */
    int n = rt->travel / STEP_THIRDS;
    if (!n) return;
    rt->travel -= n * STEP_THIRDS;
    int fast = now - rt->tick_at < (unsigned)(FAST_MS * (n < 0 ? -n : n));
    rt->tick_at = now;
    fine_turn(n, fast, 1);
}

/* The words' reveal (the prototype's shown): the station on the dial, once its stream has sounded
 * (the receiver's state playing) and its title has come, or NO_TITLE_MS of sound has passed with
 * none; up until the dial leaves it, paused or cut out included. */
static void words_due(unsigned now) {
    rt_station *here = at((float)rt->freq);
    if (rt->shown && (!here || index_of(here) + 1 != rt->shown)) rt->shown = 0;
    int locked = rt->feed >= 0 && !tk_strcmp(rt->state, "playing");
    if (locked && !rt->locked_at) rt->locked_at = now ? now : 1;
    if (rt->shown || !here || index_of(here) != rt->feed || !locked) return;
    if (!rt->title_text[0] && now - rt->locked_at <= NO_TITLE_MS) return;
    rt->shown = index_of(here) + 1, rt->words_at = now;
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
        freq *= 10; /* 100 kHz to 10 kHz */
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
        rt_station *s = at((float)rt->freq);
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
    wheel(now);
    int status,
        lost = wifi_offline(); /* the network monitor's link, as the status row's (status_row.c) */
    if (lost !=
        rt->no_wifi) { /* "No Wi-Fi" up or down; back, a stream not sounding starts again at once */
        if (!lost) rt_send("r");
        rt->no_wifi = lost;
        redraw();
    }
    if (rt->audio && waitpid(rt->audio, &status,
                             1)) /* the receiver gone (no output to open): tune starts it again */
        rt->audio = 0, rt->ended = now | 1, rt->feed = -1, rt->locked_at = 0;
    if (!rt->audio) return;
    if (now - rt->polled < STATE_MS) return;
    rt->polled = now;
    char raw[600];
    if (state_read(TUNER_STATE, raw, sizeof raw)) {
        char t[256];
        field(raw, "state", rt->state, sizeof rt->state);
        field(raw, "title", t, sizeof t);
        char url[RT_URL];
        field(raw, "url", url, sizeof url);
        /* A receiver started again binds its socket after land may have named the stream, so
         * that u is lost: named again, with the carrier (a u it already has, it ignores). */
        if (rt->feed >= 0 && tk_strcmp(url, rt->st[rt->feed].url)) {
            char u[RT_URL + 2];
            tk_snprintf(u, sizeof u, "u%s", rt->st[rt->feed].url);
            rt_send(u);
            receive();
        }
        if (tk_strcmp(t, rt->title_text) && rt->screen == TUNER) redraw();
        tk_snprintf(rt->title_text, sizeof rt->title_text, "%s", t);
    }
    words_due(now);
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

/* The scale under the needle at nd (10 kHz, fractional while gliding): a tick each 0.2 MHz (the
 * odd tenths, the US channels), longer on the whole MHz with labels on the even ones, no mark for
 * the stations (the detents and the sound find them), both ends fading out over FADE px, the
 * needle in Favorite red. */
static void dial(void *canvas, float nd) {
    const float cx = 187.5f;
    for (int m = LO; m <= HI; m += 20) {
        float x = cx + (m - nd) * PX_MHZ / 100;
        if (x < -20 || x > 395) continue;
        int whole = (m + 10) % 100 == 0, n = (m + 10) / 100;
        box(canvas, (int)(x - 0.75f + 0.5f), DIAL_Y - (whole ? 14 : 7), 2, whole ? 14 : 7,
            rgba(whole ? RGB_SECONDARY : RGB_TERTIARY, 255));
        if (whole && n % 2 == 0) {
            char l[8];
            tk_snprintf(l, sizeof l, "%d", n);
            rt_text(canvas, l, 0, 13, RGB_TERTIARY, (int)(x + 3.1f), DIAL_Y + 14, 40, 1);
        }
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

/* A StreamTitle's parts: iHeart's key="value" pairs (title="R.I.P.",artist="MOTIONLESS IN
 * WHITE",url="...") into artist, song and a spot's text= (each 128 bytes), all capitals made title
 * case (cased); 0 when it has no pairs. */
static int icy_pairs(const char *in, char *artist, char *song, char *spot) {
    int pairs = 0;
    artist[0] = song[0] = spot[0] = 0;
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
    return pairs;
}

/* A StreamTitle as the tuner shows it: "Artist - Title" as sent, or iHeart's pairs as "Artist -
 * Title", or a spot's text=; all capitals made title case (cased). Shared with test/fw. */
void tuner_title(const char *in, char *out, unsigned cap) {
    char artist[128], song[128], spot[128];
    if (!icy_pairs(in, artist, song, spot)) {
        tk_snprintf(out, cap, "%s", in);
        cased(out);
    } else if (artist[0] && song[0])
        tk_snprintf(out, cap, "%s - %s", artist, song);
    else
        tk_snprintf(out, cap, "%s", song[0] ? song : artist[0] ? artist : spot);
}

/* A StreamTitle as a car's Now Playing takes it (now_source.c): the artist and the song apart, from
 * iHeart's pairs or from "Artist - Title" (no " - ": all of it the song), title case as the
 * tuner's; a spot's text is the song. Either may be empty. */
void tuner_song(const char *in, char *artist, char *song, unsigned cap) {
    char a[128], s[128], spot[128];
    if (!icy_pairs(in, a, s, spot)) {
        const char *dash = strstr(in, " - ");
        tk_snprintf(a, sizeof a, "%.*s", dash ? (int)(dash - in) : 0, in);
        tk_snprintf(s, sizeof s, "%s", dash ? dash + 3 : in);
        cased(a), cased(s);
    } else if (!s[0] && !a[0])
        tk_snprintf(s, sizeof s, "%s", spot);
    tk_snprintf(artist, cap, "%s", a);
    tk_snprintf(song, cap, "%s", s);
}

/* What Radio plays, for now_source.c: 0 with no receiver (Radio never opened, or ended for the
 * Library), else 1 playing or 2 paused, its page open or shut. Playing is the user's Play alone
 * (the user's, 2026-11-20: "as long as in radio, whether on station or not... now playing"):
 * connecting, between stations, a dropout or the receiver restarting all read playing; only Pause
 * reads paused. station: the station's name (its call sign without one); freq the station's or
 * the dial's frequency as "93.1 FM"; title the stream's raw StreamTitle (empty between stations or
 * before its first); each in cap bytes. */
int tuner_state(void) { return !rt ? 0 : rt->playing ? 1 : 2; }
int tuner_dial(void) { return rt ? rt->freq : 0; }
int tuner_now(char *station, char *freq, char *title, unsigned cap) {
    if (!rt) return 0;
    const rt_station *s = rt->feed >= 0 ? &rt->st[rt->feed] : 0;
    tk_snprintf(station, cap, "%s", !s ? "" : s->name[0] ? s->name : s->call);
    int f = s ? s->freq : rt->freq;
    tk_snprintf(freq, cap, "%d.%d FM", f / 100, f / 10 % 10);
    tk_snprintf(title, cap, "%s", s ? rt->title_text : "");
    return tuner_state();
}

/* The house marquee on line (0 the name, 1 the song) at screen row y: centred when it fits in
 * WORDS_W; wider, still for MARQ_HOLD_MS, then scrolling left MARQ_PX px a second with a copy
 * MARQ_GAP behind, clipped to the column and its ends faded MARQ_FADE px into the black. Each line
 * keeps its own clock, which starts over when its text changes. 1 while it scrolls. */
static int marquee(void *canvas, int line, const char *s, int medium, int px, unsigned rgb, int y) {
    unsigned key = 5381, now = time_now_ms();
    for (const char *c = s; *c; ++c) key = key * 33 + (unsigned char)*c;
    if (rt->mq_key[line] != key) rt->mq_key[line] = key, rt->mq_at[line] = now;
    int w = ui_text_w(canvas, s, medium, TYPE(px));
    if (w <= WORDS_W) return rt_text(canvas, s, medium, px, rgb, 187, y, 375, 1), 0;
    unsigned el = now - rt->mq_at[line];
    int off = el < MARQ_HOLD_MS
                  ? 0
                  : (int)((el - MARQ_HOLD_MS) * MARQ_PX / 1000 % (unsigned)(w + MARQ_GAP));
    int x0 = (375 - WORDS_W) / 2, was[4], ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y);
    canvas_get_clip_rect(canvas, was);
    int col[4] = { ox + x0, oy + y - rt->y0 - px, WORDS_W, 2 * px };
    int l = col[0] > was[0] ? col[0] : was[0], t = col[1] > was[1] ? col[1] : was[1],
        r = col[0] + col[2] < was[0] + was[2] ? col[0] + col[2] : was[0] + was[2],
        b = col[1] + col[3] < was[1] + was[3] ? col[1] + col[3] : was[1] + was[3];
    if (r <= l || b <= t) return 1;
    int clip[4] = { l, t, r - l, b - t };
    canvas_set_clip_rect(canvas, clip);
    for (int k = 0; k < 2; ++k)
        rt_text(canvas, s, medium, px, rgb, x0 - off + k * (w + MARQ_GAP), y, w + 8, 2);
    for (int i = 0; i < MARQ_FADE; ++i) {
        unsigned a = (unsigned)(255 * (MARQ_FADE - i) / MARQ_FADE);
        box(canvas, x0 + i, y - px, 1, 2 * px, rgba(RGB_CANVAS, a));
        box(canvas, x0 + WORDS_W - 1 - i, y - px, 1, 2 * px, rgba(RGB_CANVAS, a));
    }
    canvas_set_clip_rect(canvas, was);
    return 1;
}

/* The words' and the marquee's frame: a repaint while the last one moved. */
static int marq_tick(const void *unused) {
    if (!rt || !rt->page || rt->screen != TUNER || !rt->live) return rt ? (int)(rt->marq = 0) : 0;
    redraw();
    return 8; /* RET_REPEAT */
}

/* The volume band in the dial's place (Centre tapped): Now Playing's (volume.c vol_paint over its
 * progress bar), on the dial's row VOL_Y: its bar's ends (the OLED grid's text_x to the right
 * column, VOL_X and VOL_W), a VOL_SPK_PX speaker VOL_SPK_X in from each, the track inset
 * VOL_SPK_INSET between them in the theme's np_track_rgb, white to the volume as vol_bar eases. */
#define VOL_X 24
#define VOL_W 322
#define VOL_Y 262
static void vol_band(void *canvas) {
    int h = theme()->np_bar ? theme()->np_bar : 6, cy = VOL_Y - rt->y0, L = ipod_light();
    rect_t bar = { VOL_X + VOL_SPK_INSET, cy - h / 2, VOL_W - 2 * VOL_SPK_INSET, h };
    int eased = clamp_step(0, 100 << 8, level_shown(&vol_bar, g_volume)),
        w = bar.w * eased / (100 << 8);
    fill_box(canvas, &bar, RGBA(theme()->np_track_rgb), (unsigned)h / 2);
    rect_t on = { bar.x, bar.y, w < h ? h : w, h };
    if (w) fill_box(canvas, &on, RGBA(0xffffff), (unsigned)h / 2);
    paint_art(rt->sheet, canvas, theme_art(L ? "vol_lo_l" : "vol_lo_d"),
              VOL_X + VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
    paint_art(rt->sheet, canvas, theme_art(L ? "vol_hi_l" : "vol_hi_d"),
              VOL_X + VOL_W - VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
}

/* The tuner (the prototype's layout): the frequency the needle is at, centred between the status
 * row and the dial; above it the station's name, below its song (or Paused) and its call sign and
 * city, once revealed (words_due) and while the needle is on it, fading in over REVEAL_MS. Between
 * stations, or before the words come, the frequency alone: the hiss says the rest. */
static void tuner_screen(void *canvas) {
    float nd = needle();
    rt_station *here = at(nd), *s = here && rt->shown == index_of(here) + 1 ? here : 0;
    int tenth = (int)(nd / 10 + 0.5f);
    char num[8];
    tk_snprintf(num, sizeof num, "%d.%d", tenth / 10, tenth % 10);
    int nw = ui_text_w(canvas, num, 1, TYPE(64)), fw = ui_text_w(canvas, "FM", 1, TYPE(17));
    int x0 = (375 - nw - 6 - fw) / 2;
    rt_text_base(canvas, num, 64, RGB_PRIMARY, x0, 158);
    rt_text_base(canvas, "FM", 17, RGB_SECONDARY, x0 + nw + 6, 158);
    rt->live = 0;
    if (s) {
        unsigned age = time_now_ms() - rt->words_at,
                 a = age >= REVEAL_MS ? 255 : age * 255 / REVEAL_MS;
        rt->live = a < 255;
        rt->live |= marquee(canvas, 0, s->name, 1, 20, RGB_PRIMARY, 84);
        char t[256];
        tuner_title(!rt->playing              ? "Paused"
                    : index_of(s) == rt->feed ? rt->title_text
                                              : "",
                    t, sizeof t);
        if (t[0]) rt->live |= marquee(canvas, 1, t, 0, 15, RGB_SECONDARY, 186);
        char two[64];
        tk_snprintf(two, sizeof two, "%s \xc2\xb7 %s", s->call, s->city);
        char *comma = strrchr(two, ','); /* the city without its state, as the prototype */
        if (comma) *comma = 0;
        rt_text(canvas, two, 0, 15, RGB_TERTIARY, 187, 208, WORDS_W, 1);
        if (a < 255) { /* the fade: the black over the words, thinning */
            box(canvas, 0, 84 - 14, 375, 28, rgba(RGB_CANVAS, 255 - a));
            box(canvas, 0, 186 - 11, 375, 44, rgba(RGB_CANVAS, 255 - a));
        }
    }
    if (rt->live && !rt->marq) rt->marq = timer_add(marq_tick, 0, MARQ_MS);
    if (rt->vol)
        vol_band(canvas);
    else
        dial(canvas, nd);
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
        tk_snprintf(f, sizeof f, "%d.%d", s->freq / 100, s->freq / 10 % 10);
        int rw = ui_text_w(canvas, f, 0, TYPE(20));
        int playing =
            streams(s) && rt->playing && index_of(s) == rt->feed && at((float)rt->freq) == s;
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
    if (!rt || !rt->page) return rt ? (int)(rt->anim = 0) : 0;
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
                  (f && rt->playing && at((float)rt->freq) == f);
    }
    moving |= rt->screen == LOADING;
    if (rt->screen == TUNER && rt->vol && level_ease(&vol_bar, g_volume)) moving = 1;
    redraw();
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

/* The band's SCRUB_MS (Now Playing's scrub and Centre-cycle timeout) since the last turn or press
 * have passed: the dial again, the wheel tuning. */
static int vol_end(const void *unused) {
    if (!rt) return 0;
    rt->vol_timer = 0, rt->vol = 0;
    animate();
    return 0;
}
static void vol_open(int on) {
    rt->vol = on;
    if (on) {
        vol_bar.easing = 0; /* the band opens on the volume */
        rearm(&rt->vol_timer, vol_end, SCRUB_MS);
    } else
        stop_timer(&rt->vol_timer);
    animate();
}

/* The stations list, on the station heard (Centre held on the tuner). */
static void list_open(void) {
    rt_station *here = at((float)rt->freq);
    rt->sel = index_of(here) > 0 ? index_of(here) : 0;
    rt->scroll = rt->target = (float)((rt->sel > 3 ? rt->sel - 3 : 0) * ROW);
    if (rt->vol) vol_open(0);
    screen(LIST);
}

/* Centre held LR_HOLD_MS on the tuner: the stations list, and stock's own Centre hold (Shut Down?
 * at 1000 ms) removed, as Edit Library's hold removes it; the release is taken (tuner_key). */
static int hold_due(const void *unused) {
    if (!rt) return 0;
    rt->hold = 0, rt->held = 1;
    if (st.centre_hold) timer_remove(st.centre_hold), st.centre_hold = 0;
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* the list's wheel at once */
    list_open();
    return 0;
}

/* ringnav_keydown: Centre pressed on the tuner starts its hold; released sooner, it is a tap
 * (tuner_key). */
void tuner_keydown(void *top, unsigned key) {
    if (!rt || top != rt->page || key != KEY_CENTER || rt->screen != TUNER || rt->hold) return;
    rt->held = 0;
    rt->hold = timer_add(hold_due, 0, LR_HOLD_MS);
}

/* ringnav (nav/input.c): the page's keys, before stock's; 0 for those it leaves to stock (Return on
 * the tuner, which leaves the page through keyup). */
int tuner_key(void *top, unsigned key) {
    /* Play on the Library's own Now Playing while Radio owns it: the Library chosen again, Radio
     * ended and the Library's song playing on from where Radio held it (now_take_back) */
    if (rt && key == KEY_PLAY && top && top != rt->page && !play_hold_released() &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page")) {
        tuner_stop();
        now_take_back();
        return 1;
    }
    if (!rt || !rt->page || top != rt->page) return 0;
    if (key == KEY_RETURN && *(volatile unsigned char *)RETURN_RELEASE_LATCH) return 0;
    int wheel = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0, s = rt->screen;
    if (key == KEY_CENTER && rt->held) { /* the release of the hold that opened the list */
        rt->held = 0;
        return 1;
    }
    if (key == KEY_CENTER) stop_timer(&rt->hold);
    if (wheel) rt->tap_armed = 0; /* a turn between two taps: not a double press */
    if (key == KEY_PLAY && (s == TUNER || s == LIST)) {
        if (!rt->playing && check_forbid_play_no_headset(0)) return 1; /* output_rule.c */
        rt->playing = !rt->playing;
        tune();
        return 1;
    }
    if (s == TUNER) {
        if (key == KEY_RETURN) return 0;
        if (wheel && rt->vol) { /* the volume, as Now Playing's wheel sets it (vol_next) */
            int v = vol_next(key);
            if (v != g_volume) {
                g_volume = (unsigned char)v;
                device_set_volume(v, 1);
                write_int_config(v, "PLAYSET", "VOLUME");
            }
            vol_open(1);    /* its SCRUB_MS from this turn */
        } else if (wheel) { /* a tick a step: the touch wheel itself is read raw (wheel) */
            unsigned now = time_now_ms();
            int fast = now - rt->tick_at < FAST_MS;
            rt->tick_at = now;
            fine_turn(wheel, fast, 0);
        } else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
            seek(key == KEY_FWD_BTN ? 1 : -1);
        else if (key == KEY_CENTER) { /* a tap: the volume shown, or a tap while it shows: gone */
            unsigned now = time_now_ms();
            int dbl = rt->tap_armed && now - rt->tap_at < DOUBLE_CLICK_MS;
            rt->tap_armed = !dbl, rt->tap_at = now;
            vol_open(!rt->vol);              /* a double press: the first undone ... */
            if (dbl && g_backlight_status) { /* ... and the screen off, as on every page */
                static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
                on_wm_keyup_fun((void *)0, (void *)release); /* stock's Centre release */
            }
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

/* A finger: on the tuner's dial (y 225 down) a tap tunes to the frequency under it, to 0.01 MHz,
 * landing on a station within TAP_SNAP, and a drag scrubs, the scale following it, a station
 * within CAPTURE pulling it in, each move a click; on the list a tap acts on the row. */
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
        int f = rt->tf - (x - rt->tx) * 100 / PX_MHZ; /* the scale moves under a fixed needle */
        f = f < LO ? LO : f > HI ? HI : f;
        rt_station *st = 0;
        for (int i = 0; i < rt->n && !st; ++i)
            if (rt->st[i].freq - f <= CAPTURE && f - rt->st[i].freq <= CAPTURE) st = &rt->st[i];
        if (st) f = st->freq; /* the detent pulls a scrub in too */
        if (f != rt->freq) rt->freq = f, click(st ? 14 : 5), tune();
        return 11; /* RET_STOP */
    }
    if (type == EVT_POINTER_UP_BEFORE) {
        rt->touch = 0;
        if (rt->screen == TUNER && !rt->moved) {
            int f = rt->freq + (x - 187) * 100 / PX_MHZ;
            rt_station *best = 0;
            for (int i = 0; i < rt->n; ++i) {
                int d = rt->st[i].freq - f;
                if (d < 0) d = -d;
                if (d <= TAP_SNAP &&
                    (!best || d < (best->freq > f ? best->freq - f : f - best->freq)))
                    best = &rt->st[i];
            }
            f = best ? best->freq : f;
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
    child_quit(rt->audio, QUIT_MS); /* radio.c */
    rt->audio = 0;
}

/* Radio's end: its timers, the wheel's ticks back, the receiver asked to quit, the state freed. */
static void teardown(void) {
    stop_timer(&rt->anim), stop_timer(&rt->land), stop_timer(&rt->warm), stop_timer(&rt->leave);
    stop_timer(&rt->marq), stop_timer(&rt->vol_timer), stop_timer(&rt->hold);
    if (rt->raw && wheel_raw == WHEEL_RAW) wheel_raw = WHEEL_TICKS; /* the wheel's ticks back */
    audio_halt();
    if (rt->sock > 0) close(rt->sock);
    free(rt);
    rt = 0;
}

/* The page shut (Back, Home, another page over it): Radio plays on, the owner of Now Playing
 * (now_source.c), its station and its place kept for the page's return; only a page that found no
 * stations ends with it, the Library's song back paused where Radio found it (now_release). */
static int closed(void *ctx, void *event) {
    if (!rt) return 0;
    stop_timer(&rt->anim), stop_timer(&rt->leave), stop_timer(&rt->marq);
    stop_timer(&rt->vol_timer), stop_timer(&rt->hold);
    rt->vol = rt->held = 0;
    if (rt->raw && wheel_raw == WHEEL_RAW) wheel_raw = WHEEL_TICKS, rt->raw = 0;
    rt->page = rt->sheet = rt->title = 0;
    if (rt->n && !rt->ending) return 0;
    teardown();
    now_release(); /* the Library's song back, paused where Radio found it */
    return 0;
}

/* Radio ends for the Library (tuner_yield, tuner_key's Play on the Library's Now Playing): its
 * station kept for next time, the page shut if it is open. */
void tuner_stop(void) {
    if (!rt) return;
    freq_save();
    if (rt->page) {
        rt->ending = 1;
        window_close(rt->page); /* closed */
    }
    if (rt) teardown();
}

/* A Library pick (playback.c ringnav_load): Radio ends, and the Library's held song goes with it,
 * the pick playing in its place (now_drop). */
void tuner_yield(void) {
    if (!rt) return;
    tuner_stop();
    now_drop();
}

/* Play or pause from elsewhere (headset.c media_set_playing and media_key: a headset, the car, Car
 * Remote), as Play/Pause on the page. */
void tuner_set_playing(int playing) {
    if (!rt || !rt->playing == !playing) return;
    if (playing && check_forbid_play_no_headset(0)) return; /* nothing to play to (output_rule.c) */
    rt->playing = playing;
    tune();
}
/* A media key for Radio as the owner: Play/Pause, Next and Previous seek. */
void tuner_media(unsigned key) {
    if (!rt) return;
    if (key == KEY_PLAY)
        tuner_set_playing(!rt->playing);
    else if (key == KEY_FWD_BTN || key == KEY_BACK_BTN)
        seek(key == KEY_FWD_BTN ? 1 : -1);
}

/* The page's widgets, on rt (a new one, or the one playing on since its page shut). */
static int page_make(void) {
    void *page = rt->page = page_open("tuner_page", closed, keyup);
    if (!page) return 0;
    rt->y0 = bar_foot(page);
    rt->sheet = widget_factory_create_widget(widget_factory(), "view", page, 0, rt->y0, 375,
                                             page_room(page));
    rt->title = page_title(page, "Radio", 1);
    widget_on(rt->sheet, EVT_POINTER_DOWN, touch, 0);
    widget_on(page, EVT_POINTER_MOVE_BEFORE, touch, 0);
    widget_on(page, EVT_POINTER_UP_BEFORE, touch, 0);
    return 1;
}

/* Home's Radio row (and Now Playing's ways in while Radio owns it): the page. On Radio playing on
 * since its page shut, the page again on its tuner where it was, the same receiver; else the
 * receiver started (whatever else held the output stopped), the dial on the station last landed on
 * (FREQ_FILE), and the stations for the chosen ZIP or the network's place looked up. With nothing to
 * play to, a fresh one opens nothing (check_forbid_play_no_headset's message instead). */
int tuner_open(void *ctx, void *event) {
    if (rt && rt->page) return 0;
    if (rt) {
        if (!page_make()) return 0;
        screen(rt->screen == LIST || rt->screen == ZIP ? TUNER : rt->screen);
        tune();
        return 0;
    }
    /* nothing to play to: the page does not open, as stock's Now Playing closes (output_rule.c) */
    if (check_forbid_play_no_headset(0)) return 0;
    if (!(rt = calloc(1, sizeof *rt))) return 0;
    if (!page_make()) {
        free(rt);
        rt = 0;
        return 0;
    }
    rt->freq = 9310, rt->playing = 1, rt->feed = -1, rt->sock = -1;
    char z[8] = "";
    void *f = fopen(FREQ_FILE, "rb");
    if (f) z[fread(z, 1, 6, f)] = 0, fclose(f);
    int saved = atoi(z);
    if (saved >= LO && saved <= HI) rt->freq = saved;
    headset_manual();
    spot_yield(); /* librespot, Internet Radio and the player hold the output too */
    radio_stop();
    smart_wake(); /* Smart Wi-Fi's radio back now; tuner_page holds it on (smart_wifi.c
                     net_page_open) */
    now_hold();   /* the Library paused where it was, not stopped and forgotten (now_source.c) */
    rt->audio = video_start("-", 2);
    rt->volume = headset_gain() + 1;
    z[0] = 0;
    f = fopen(ZIP_FILE, "rb");
    if (f) z[fread(z, 1, 5, f)] = 0, fclose(f);
    tk_snprintf(rt->zip_text, sizeof rt->zip_text, "%s", strlen(z) == 5 ? z : "");
    find(rt->zip_text[0] ? rt->zip_text : 0);
    return 0;
}
