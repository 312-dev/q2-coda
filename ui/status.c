/* The status row (docs/oled-design.md#status-row): an overlay on every page with no band and no
 * fill, drawn after the page so its glyphs stand over whatever runs under them. At the left the
 * clock, Inter Medium 15 from x 62, its figures on rows 17 to 27, in Text Secondary, and in Text
 * Primary on Home. At the right, right to left, each only while it has something to say: the
 * battery, its nub ending 62 px from the edge; the play state; Wi-Fi's fan; a card app's sync;
 * wired headphones, or the deaf ear with no output; Bluetooth; Buttons Lock's padlock; a card
 * app's server out of reach. Between them, for a moment after the volume changes, its level.
 *
 * Where each comes from: the battery from the kernel's power supply files; the play state from
 * what plays (now.c); Wi-Fi from wlan0's state and its line in /proc/net/wireless; all read here.
 * The rest are demo's alone while it owns the services, so demo says them in a file of
 * key=value lines (status_told, /tmp/q2ui.status, patch/q2ui_gate.c tell and status_row.c
 * status_row_state), written when they change and read here when it is (now.c's watch of its
 * folder). The kernel's files are read when the minute turns and when what plays changes, never on
 * a timer of their own, so the row costs nothing at rest; a cable put in shows within the minute.
 * Only the sync's arrows move, 30 frames a second while a card app checks its server. The
 * theme's glyphs are tools/art.py's PNGs, in the half for Light Mode; on Home, over the art, in one ink picked
 * for the row from the art under them (glyphs_over_art). ponytail: a strip too wide
 * for its room is cut at the clock, not slid as demo's marquee slides it. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "q2ui.h"

#define INSET 62
#define CLOCK_Y 14 /* the 18 px line box whose figures stand on rows 17 to 27 */
#define CLOCK_W 64
#define RIGHT (SCREEN_W - INSET) /* the battery's nub ends here */
#define BATT_W 25
#define BATT_H 12
#define BATT_Y 16
#define GAP 6
#define LOW 20 /* under this the charge is red */

const char *status_power = "/sys/class/power_supply", *status_wifi = "/sys/class/net/wlan0/operstate",
           *status_wireless = "/proc/net/wireless", *status_told = "/tmp/q2ui.status";
static struct {
    char clock[8];
    run_t run;
    int fixed;
    long at;          /* the clock's time, a script's (status_clock_at); 0: the system's */
    int clock24;      /* demo's 24-hour setting: 1 "18:14", 0 "6:14" (no AM or PM, as demo's status row) */
    char tz[64];      /* demo's time zone, its TZ, as it last said */
    int level, cable; /* the battery's percent, -1 unknown; 1 on a cable that charges or holds it */
    int wifi;         /* 0 not connected, 1 to 3 the fan's lit parts */
    int play;         /* 0 stopped, 1 playing, 2 paused */
    int volume;       /* the level last seen, -1 before the first */
    uint64_t volume_at; /* when it last changed on a page with no display of its own, 0: not shown */
    unsigned seen;    /* now_seq() when play was read */
    /* demo's word (status_told): where the sound goes, the Bluetooth slot and a headset's level,
     * a guest's relay, the lock, a sync and its permille, a server out of reach, Wi-Fi's internet */
    char out[8], bt[12], relay[12];
    int btbatt, lock, sync, syncpm, offline, net;
    int told;         /* status_told changed since it was read */
    char told_text[1536]; /* the file as last read, for status_told_value: a cover's path is up to 600 */
    int spin;         /* the sync arrows' frame shown */
} S = { .level = -1, .volume = -1, .btbatt = -1, .net = 1, .told = 1, .clock24 = 1 };

#define VOLUME_SHOW_US 1500000u
#define VOLUME_X 150 /* its track, between the clock and the glyphs */
#define VOLUME_W 76
static rect_t volume_rect(void) { return (rect_t){ VOLUME_X - 2, 18, VOLUME_W + 4, 10 }; }

/* The clock reads hhmm whatever the time: a script's, so a picture is the same on every run. */
void status_clock_fixed(const char *hhmm) {
    snprintf(S.clock, sizeof S.clock, "%s", hhmm);
    S.fixed = 1, S.run.n = 0;
}
/* The clock reads the time at epoch, in demo's zone and form: a script's, so the zone and the form are what is tried */
void status_clock_at(long epoch) { S.at = epoch, S.fixed = 0, S.clock[0] = 0; }
/* The time the clock reads: a script's, else the system's (Light Mode's and Night Shift's Auto read it too) */
long status_time(void) { return S.at ? S.at : (long)time(0); }

rect_t status_rect(void) { return (rect_t){ INSET, CLOCK_Y, SCREEN_W - 2 * INSET, 18 }; }
/* everything right of the clock: the slots take what room they need, right to left */
#define STRIP_X (INSET + CLOCK_W + 8)
static rect_t glyphs_rect(void) { return (rect_t){ STRIP_X, CLOCK_Y, RIGHT - STRIP_X, 18 }; }

static int file_word(const char *path, char *out, size_t room) {
    FILE *f = fopen(path, "r");
    *out = 0;
    if (!f) return 0;
    if (!fgets(out, (int)room, f)) *out = 0;
    fclose(f);
    out[strcspn(out, "\r\n")] = 0;
    return *out != 0;
}
static void read_glyphs(void) {
    char path[200], word[64];
    int level = -1, cable = 0, wifi = 0;
    snprintf(path, sizeof path, "%s/battery/capacity", status_power);
    if (file_word(path, word, sizeof word)) level = atoi(word);
    snprintf(path, sizeof path, "%s/battery/status", status_power);
    if (file_word(path, word, sizeof word)) cable = !strcmp(word, "Charging") || !strcmp(word, "Full");
    snprintf(path, sizeof path, "%s/ac/online", status_power);
    if (file_word(path, word, sizeof word) && atoi(word) == 1) cable = 1;
    if (file_word(status_wifi, word, sizeof word) && !strcmp(word, "up")) {
        /* the link's quality out of 70, as the kernel's wireless line has it: three parts of the fan */
        FILE *f = fopen(status_wireless, "r");
        float quality = 70;
        for (char line[160]; f && fgets(line, sizeof line, f);)
            if (strstr(line, "wlan0:")) sscanf(strchr(line, ':') + 1, "%*s %f", &quality);
        if (f) fclose(f);
        wifi = quality >= 45 ? 3 : quality >= 25 ? 2 : 1;
    }
    if (level != S.level || cable != S.cable || wifi != S.wifi) {
        S.level = level, S.cable = cable, S.wifi = wifi;
        ui_mark(glyphs_rect());
    }
}

/* demo's file changed (now.c's watch): read it at the next step */
void status_told_changed(void) { S.told = 1; }

static void word_of(const char *line, const char *key, char *out, size_t room) {
    size_t k = strlen(key);
    if (!strncmp(line, key, k) && line[k] == '=') snprintf(out, room, "%.*s", (int)strcspn(line + k + 1, "\r\n"), line + k + 1);
}
static void read_told(void);
/* The link for Control Center's Wi-Fi circle (page_control.c), read again when a second has gone by since it
 * was, as demo polls it for its own circle (control_center.c cc_wifi_poll) */
int status_wifi_bars(void) {
    static uint64_t read_at;
    if (!read_at || ui_time() - read_at >= 1000000u) read_at = ui_time() | 1, read_glyphs();
    if (S.told) S.told = 0, read_told();
    return S.wifi && !S.net ? 4 : S.wifi;
}
/* demo's word on key (Control Center's switches too, page_control.c) into out; 0 when it said nothing.
 * The file is read first if it changed since, so a page asking before the first frame (a page a
 * start comes back to, main.c state_restore, counting its rows) has demo's word too. */
int status_told_value(const char *key, char *out, size_t room) {
    size_t k = strlen(key);
    if (S.told) S.told = 0, read_told();
    *out = 0;
    for (const char *line = S.told_text; *line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : "")
        if (!strncmp(line, key, k) && line[k] == '=') {
            snprintf(out, room, "%.*s", (int)strcspn(line + k + 1, "\r\n"), line + k + 1);
            return 1;
        }
    return 0;
}

static void read_told(void) {
    char line[640], num[12], text[sizeof S.told_text]; /* a line: up to the cover's path (now.c) */
    FILE *f = fopen(status_told, "r");
    size_t got = f ? fread(text, 1, sizeof text - 1, f) : 0;
    text[got] = 0;
    if (strcmp(text, S.told_text)) { /* a page may show it (Control Center's switches): every row asked again */
        memcpy(S.told_text, text, sizeof text);
        list_rows_changed(), ui_mark_all();
    }
    if (f) rewind(f);
    char out[8] = "", bt[12] = "", relay[12] = "", tz[sizeof S.tz] = "", c24[4] = "";
    int v[6] = { -1, 0, 0, 0, 0, 1 }; /* btbatt lock sync syncpm offline net */
    static const char *const keys[] = { "btbatt", "lock", "sync", "syncpm", "offline", "net" };
    while (f && fgets(line, sizeof line, f)) {
        word_of(line, "out", out, sizeof out);
        word_of(line, "bt", bt, sizeof bt);
        word_of(line, "relay", relay, sizeof relay);
        word_of(line, "tz", tz, sizeof tz);
        word_of(line, "clock24", c24, sizeof c24);
        for (int k = 0; k < 6; ++k) {
            *num = 0, word_of(line, keys[k], num, sizeof num);
            if (*num) v[k] = atoi(num);
        }
    }
    if (f) fclose(f);
    /* the clock in demo's zone and form (nav/lyrics_status_bar.c clock_show): a zone it names is set for every
     * localtime here; none said keeps the one there is */
    if (*tz && strcmp(tz, S.tz)) {
        snprintf(S.tz, sizeof S.tz, "%s", tz);
        setenv("TZ", tz, 1);
        tzset();
        S.clock[0] = 0;
    }
    if (*c24 && atoi(c24) != S.clock24) S.clock24 = atoi(c24), S.clock[0] = 0;
    if (strcmp(out, S.out) || strcmp(bt, S.bt) || strcmp(relay, S.relay) || v[0] != S.btbatt || v[1] != S.lock ||
        v[2] != S.sync || v[3] != S.syncpm || v[4] != S.offline || v[5] != S.net) {
        snprintf(S.out, sizeof S.out, "%s", out), snprintf(S.bt, sizeof S.bt, "%s", bt);
        snprintf(S.relay, sizeof S.relay, "%s", relay);
        S.btbatt = v[0], S.lock = v[1], S.sync = v[2], S.syncpm = v[3], S.offline = v[4], S.net = v[5];
        ui_mark(glyphs_rect());
    }
}

/* The slots' boxes, patch/status_row.c's SR_* (tools/art.py's widths) */
#define WIFI_W 16
#define GLYPH 14   /* a glyph's box: wired headphones, the offline cloud */
#define EAR_W 10   /* the deaf ear */
#define RUNE_W 9   /* the Bluetooth rune */
#define CAR_W 19
#define LOCK_W 13
#define RING_W 16  /* the download's ring */
#define SPIN_W 12  /* the turning arrows */
#define BT_GAP 2   /* between headphones and the headset's battery, and round a guest's relay */
#define HS_BATT_W 15
#define HS_NUB 2
#define RELAY_W 8
/* Where the slots left of the battery start: the battery's body on whole pixels and GAP before it,
 * so the glyphs' 2 and 3 px strokes do not straddle pixels. */
static float after_battery(void) { return S.level >= 0 ? floorf(RIGHT - 2.5f - BATT_W) - GAP : RIGHT; }
/* ... and demo's slots, past the play state and Wi-Fi */
static float sync_right(void) { return after_battery() - (S.play ? 10 + GAP : 0) - (S.wifi ? WIFI_W + GAP : 0); }

#define SPIN_FRAMES 30 /* tools/art.py STATUS_SPIN: the arrows' frames a half turn, a half turn a second */
#define SPIN_US (1000000u / SPIN_FRAMES)

void status_step(void) {
    char s[8];
    int play = now_get()->playing ? 1 : now_get()->paused ? 2 : 0;
    static int read_once;
    if (play != S.play) S.play = play, ui_mark(glyphs_rect());
    /* the volume, whoever changed it (the side buttons, a headset, a phone): its level shows in the
     * row for a moment on every page but Now Playing, which shows it in its own bar */
    int volume = now_get()->read_us ? now_get()->volume : -1;
    if (S.volume >= 0 && volume >= 0 && volume != S.volume && strcmp(ui_top_name(), "now")) {
        S.volume_at = ui_time();
        ui_mark(volume_rect());
        ui_wake_ms(VOLUME_SHOW_US / 1000u);
    }
    if (volume >= 0) S.volume = volume;
    if (S.volume_at && ui_time() - S.volume_at >= VOLUME_SHOW_US) S.volume_at = 0, ui_mark(volume_rect());
    if (!read_once) read_once = 1, read_glyphs();
    if (S.told) S.told = 0, read_told();
    if (S.sync == 1) { /* the arrows turn: their box alone again at each frame of theirs, 30 a second */
        uint64_t t = ui_time();
        int frame = (int)(t / SPIN_US % SPIN_FRAMES);
        if (frame != S.spin) S.spin = frame, ui_mark((rect_t){ (int)sync_right() - SPIN_W - 2, CLOCK_Y, SPIN_W + 4, 18 });
        ui_wake_ms((unsigned)((SPIN_US - t % SPIN_US + 999) / 1000));
    }
    if (S.fixed) return;
    time_t t = (time_t)status_time();
    struct tm *tm = localtime(&t);
    if (S.clock24)
        snprintf(s, sizeof s, "%02d:%02d", tm->tm_hour, tm->tm_min);
    else
        snprintf(s, sizeof s, "%d:%02d", (tm->tm_hour + 11) % 12 + 1, tm->tm_min);
    if (!strcmp(s, S.clock)) return;
    memcpy(S.clock, s, sizeof S.clock);
    S.run.n = 0;
    ui_mark((rect_t){ INSET, CLOCK_Y, CLOCK_W, 18 });
    read_glyphs(); /* the minute turned: the loop is awake anyway */
    config_light_now(); /* Light Mode's Auto turns on the hour */
}

/* Wi-Fi's fan: a quarter circle standing on its point, cut into a dot and two bands, the lit ones
 * in ink and the rest in the same ink at UNLIT, iOS's rule (tools/art.py STATUS_UNLIT). Drawn a pixel
 * at a time by how far it is from the point and how far off upright, its edges smoothed over a pixel. */
#define UNLIT 0.3f
static void fan(float right, float base, int lit, uint32_t ink) {
    static const float from[] = { 0, 5.0f, 8.7f }, to[] = { 3.6f, 7.3f, 11.0f };
    float ax = right - 7.8f, ay = base;
    rect_t c, box = { (int)floorf(ax - 8), (int)floorf(ay - 12), 17, 13 };
    if (!rect_meet(box, clip_get(), &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y)
        for (int x = c.x; x < c.x + c.w; ++x) {
            float dx = (float)x + 0.5f - ax, dy = ay - ((float)y + 0.5f), r = sqrtf(dx * dx + dy * dy);
            float side = clampf((dy - fabsf(dx)) * 0.7071f + 0.5f, 0, 1); /* inside the 90 degrees */
            for (int k = 0; k < 3 && side > 0; ++k) {
                float cov = clampf(r - from[k] + 0.5f, 0, 1) * clampf(to[k] - r + 0.5f, 0, 1) * side;
                if (cov <= 0) continue;
                uint32_t *p = canvas + y * SCREEN_W + x;
                *p = 0xff000000 | rgb_mix(*p & 0xffffff, ink, k < lit ? cov : cov * UNLIT);
            }
        }
}

/* A theme glyph of the status row in the light's half (tools/art.py status_images: oled_<stem>_d or
 * _l; a stem ending in '.' is the one file both halves share), its box's left at x and its top on
 * y: on row 16 a 14 px box's ink stands on the digits' rows. A theme without it draws nothing. */
static void art(const char *stem, float x, float y) {
    char name[32];
    snprintf(name, sizeof name, strchr(stem, '.') ? "%s" : "%s_%c.", stem, ui_light ? 'l' : 'd');
    const image_t *im = icon(name);
    if (im) image_draw(im, (rectf_t){ x, y, (float)im->w, (float)im->h }, 0, 0, 256, -1);
}

/* The download's ring: a 14 px ring (1.6 px) in #333333 filling clockwise from 12 o'clock in the
 * accent to permille, its arrow in it (demo's sync_paint) */
static void ring(float left, int permille) {
    float cx = left + 8, cy = 23, share = (float)permille / 1000;
    rect_t c, box = { (int)left, 14, 17, 18 };
    if (!rect_meet(box, clip_get(), &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y)
        for (int x = c.x; x < c.x + c.w; ++x) {
            float dx = (float)x + 0.5f - cx, dy = (float)y + 0.5f - cy, r = sqrtf(dx * dx + dy * dy);
            float cov = clampf(0.8f + 0.5f - fabsf(r - 7.0f), 0, 1);
            if (cov <= 0) continue;
            float turn = atan2f(dx, -dy) / 6.2831853f; /* 0 at 12 o'clock, clockwise */
            if (turn < 0) turn += 1;
            uint32_t *p = canvas + y * SCREEN_W + x;
            *p = 0xff000000 | rgb_mix(*p & 0xffffff, turn < share ? RGB_ACCENT : 0x333333, cov);
        }
    const image_t *down = icon(ui_light ? "status_down_l." : "status_down_d.");
    if (down) image_draw(down, (rectf_t){ cx - (float)down->w / 2, cy - (float)down->h / 2, (float)down->w, (float)down->h }, 0, 0, 256, -1);
}

/* A headset's own battery: a 15 x 8 body with a 1 px outline, filled 2 px inside it to the level,
 * red under a fifth, and its nub (patch/list_marks.c battery_slim) */
static void slim(float x, int level) {
    fill_round((rectf_t){ x, 19, 15, 8 }, 4, 0, 0x6e6e6e, 1, 0);
    fill_round((rectf_t){ x + 1, 20, 13, 6 }, 3, 0, tok(TOK_CANVAS), 1, 0);
    float full = 11 * clampf((float)level / 100, 0, 1);
    if (full > 0) fill_round((rectf_t){ x + 2, 21, full, 4 }, full < 4.4f ? full / 2 : 2.2f, 0, level < LOW ? tok(TOK_NEGATIVE) : tok(TOK_PRIMARY), 1, 0);
    fill_round((rectf_t){ x + 16, 20.8f, 1.5f, 4.4f }, 0.8f, 0, 0x6e6e6e, 1, 0);
}

/* Everything right of the clock, in the inks of the half in use */
static void glyphs(void) {
    uint32_t ink = tok(TOK_PRIMARY);
    if (S.volume_at) {
        fill_round((rectf_t){ VOLUME_X, 21, VOLUME_W, 4 }, 2, 0, tok(TOK_TRACK), 1, 0);
        if (S.volume > 0) fill_round((rectf_t){ VOLUME_X, 21, 4 + (VOLUME_W - 4) * (float)S.volume / 100, 4 }, 2, 0, ink, 1, 0);
    }
    float x = RIGHT;
    if (S.level >= 0) {
        /* the nub, the body's outline, and the charge 2 px inside it to the level */
        fill_round((rectf_t){ x - 1.5f, BATT_Y + 3.8f, 1.5f, 4.4f }, 0.8f, 0, 0x6e6e6e, 1, 0);
        x -= 1.5f + 1;
        rectf_t body = { x - BATT_W, BATT_Y, BATT_W, BATT_H };
        fill_round(body, 4, 0, 0x6e6e6e, 1, 0);
        fill_round((rectf_t){ body.x + 1, body.y + 1, body.w - 2, body.h - 2 }, 3, 0, tok(TOK_CANVAS), 1, 0);
        float full = (body.w - 4) * clampf((float)S.level / 100, 0.08f, 1);
        uint32_t charge = S.cable ? tok(TOK_POSITIVE) : S.level < LOW ? tok(TOK_NEGATIVE) : ink;
        fill_round((rectf_t){ body.x + 2, body.y + 2, full, body.h - 4 }, 2.2f, 0, charge, 1, 0);
    }
    x = after_battery();
    if (S.play == 1) { /* three level bars standing on the digits' baseline */
        static const float high[] = { 7, 11, 5 };
        for (int k = 0; k < 3; ++k) fill_round((rectf_t){ x - 10 + (float)k * 4, 28 - high[k], 2, high[k] }, 1, 0, ink, 1, 0);
    } else if (S.play == 2) { /* the pause: two 3 px bars */
        fill_round((rectf_t){ x - 10, 17, 3, 11 }, 0.9f, 0, ink, 1, 0);
        fill_round((rectf_t){ x - 4, 17, 3, 11 }, 0.9f, 0, ink, 1, 0);
    }
    if (S.play) x -= 10 + GAP;
    if (S.wifi && !S.net) /* linked, with no internet: the fan crossed out */
        art("status_wifi_x", x - WIFI_W, 16);
    else if (S.wifi)
        fan(x, 28, S.wifi, ink);
    /* demo's word from here (status_told), right to left, GAP between */
    x = sync_right();
    if (S.sync == 2)
        ring(x - RING_W, S.syncpm), x -= RING_W + GAP;
    else if (S.sync == 1) {
        char stem[24];
        snprintf(stem, sizeof stem, "status_sync_%d", S.spin);
        art(stem, x - SPIN_W - 1, 16), x -= SPIN_W + GAP; /* frame 0's ink is 12 px of its 14 px box */
    }
    if (!strcmp(S.out, "wire"))
        art("status_wired", x - GLYPH, 16), x -= GLYPH + GAP;
    else if (!strcmp(S.out, "none"))
        art("status_noaudio", x - EAR_W, 16), x -= EAR_W + GAP;
    if (!strcmp(S.bt, "connecting") || !strcmp(S.bt, "rune"))
        art(S.bt[0] == 'c' ? "status_btdim" : "status_bt", x - RUNE_W, 16), x -= RUNE_W + GAP;
    else if (!strcmp(S.bt, "car"))
        art("status_car", x - CAR_W, 16), x -= CAR_W + GAP;
    else if (!strcmp(S.bt, "headset")) { /* headphones, then the headset's own battery */
        slim(x - HS_BATT_W - HS_NUB, S.btbatt);
        art("status_headphones", x - HS_BATT_W - HS_NUB - BT_GAP - GLYPH, 16);
        x -= GLYPH + BT_GAP + HS_BATT_W + HS_NUB + GAP;
    } else if (!strcmp(S.bt, "guest")) { /* a guest's phone in the accent, then › and where it plays */
        float w = !strcmp(S.relay, "car") ? CAR_W : !strcmp(S.relay, "headphones") ? GLYPH : 0;
        if (w) {
            art(w == CAR_W ? "status_car" : "status_headphones", x - w, 16);
            art("status_relay", x - w - BT_GAP - RELAY_W, 19); /* 8 px, on the row's centre */
            x -= w + BT_GAP + RELAY_W + BT_GAP;
        }
        art("status_guest.", x - GLYPH, 16), x -= GLYPH + GAP;
    }
    if (S.lock) art("keylock", x - LOCK_W, 16), x -= LOCK_W + GAP;
    if (S.offline) art("status_offline", x - GLYPH, 16);
}

/* Home's glyphs over the art (docs/oled-design.md#status-row, patch/coverflow.c home_ink): where the pane runs up
 * under the row, every glyph is drawn as the dark half draws it on a black ground, and then laid over the art in
 * one ink for the whole row, white or black, whichever has the higher WCAG contrast against the art's mean
 * luminance under all of their inked pixels together, each weighed by its share (its greatest channel: an
 * antialiased edge, the battery's dim outline, a part), held 0.03 past the bound against flicker. A status colour
 * (the charging green, the low red, the accent's ring) keeps its hue, laid over the art as it was drawn. */
#define STRIP_Y0 12
#define STRIP_Y1 34
#define STRIP_W (SCREEN_W - HOME_PANE_X)
#define INK_WHITE_MAX 0.17913f /* where black and white have the same contrast: (L + 0.05)^2 = 0.0525 */
#define INK_HOLD 0.03f
#define INK_HUE_MIN 48 /* a pixel with a spread of channels this wide is a status colour's */
static int over_ink; /* the ink the row took: 1 white, 2 black, 0 not yet picked */
static float srgb_lin(unsigned v) {
    float c = (float)v / 255;
    return c <= 0.04045f ? c / 12.92f : powf((c + 0.055f) / 1.055f, 2.4f);
}
static float lum(uint32_t p) { return 0.2126f * srgb_lin(p >> 16 & 255) + 0.7152f * srgb_lin(p >> 8 & 255) + 0.0722f * srgb_lin(p & 255); }
int status_over_ink(void) { return over_ink; }
static void glyphs_over_art(rect_t part, int whole) {
    static uint32_t art_px[(STRIP_Y1 - STRIP_Y0) * STRIP_W];
    for (int y = part.y; y < part.y + part.h; ++y)
        for (int x = part.x; x < part.x + part.w; ++x) {
            art_px[(y - STRIP_Y0) * STRIP_W + x - HOME_PANE_X] = canvas[y * SCREEN_W + x];
            canvas[y * SCREEN_W + x] = 0xff000000;
        }
    int light = ui_light;
    ui_light = 0;
    glyphs();
    ui_light = light;
    /* a repaint of part of the row shows too little of it to pick from: it keeps the ink it has */
    if (whole || !over_ink) {
        float sum = 0, weight = 0;
        for (int y = part.y; y < part.y + part.h; ++y)
            for (int x = part.x; x < part.x + part.w; ++x) {
                uint32_t p = canvas[y * SCREEN_W + x];
                unsigned r = p >> 16 & 255, g = p >> 8 & 255, b = p & 255, hi = r > g ? (r > b ? r : b) : (g > b ? g : b),
                         lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
                if (!hi || hi - lo >= INK_HUE_MIN) continue;
                float c = (float)hi / 255;
                sum += c * lum(art_px[(y - STRIP_Y0) * STRIP_W + x - HOME_PANE_X]), weight += c;
            }
        if (weight > 0) {
            float bound = over_ink == 1 ? INK_WHITE_MAX + INK_HOLD : over_ink == 2 ? INK_WHITE_MAX - INK_HOLD : INK_WHITE_MAX;
            over_ink = sum <= weight * bound ? 1 : 2;
        }
    }
    uint32_t ink = over_ink == 2 ? 0x000000 : 0xffffff;
    for (int y = part.y; y < part.y + part.h; ++y)
        for (int x = part.x; x < part.x + part.w; ++x) {
            uint32_t p = canvas[y * SCREEN_W + x], a = art_px[(y - STRIP_Y0) * STRIP_W + x - HOME_PANE_X] & 0xffffff;
            unsigned r = p >> 16 & 255, g = p >> 8 & 255, b = p & 255, hi = r > g ? (r > b ? r : b) : (g > b ? g : b),
                     lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
            float c = (float)hi / 255;
            if (hi - lo >= INK_HUE_MIN) { /* a status colour, over the art as it was drawn over the ground */
                unsigned ar = a >> 16 & 255, ag = a >> 8 & 255, ab = a & 255;
                canvas[y * SCREEN_W + x] = 0xff000000 | (unsigned)(r + ar * (1 - c) + 0.5f) << 16 |
                                           (unsigned)(g + ag * (1 - c) + 0.5f) << 8 | (unsigned)(b + ab * (1 - c) + 0.5f);
            } else
                canvas[y * SCREEN_W + x] = 0xff000000 | rgb_mix(a, ink, c);
        }
}

void status_draw(rect_t r) {
    if (!rect_meet(r, status_rect(), 0)) return;
    int home = !strcmp(ui_top_name(), "home");
    if (!S.run.n || run_stale(&S.run)) run_make(&S.run, FACE_MEDIUM, TYPE(15), S.clock, -1);
    /* on Home the clock stands over the list in Text Primary (docs/oled-design.md#status-row) */
    run_draw(&S.run, INSET, CLOCK_Y, tok(home ? TOK_PRIMARY : TOK_SECONDARY), 256);
    if (!home) {
        glyphs();
        return;
    }
    rect_t was = clip_get(), part, strip = { HOME_PANE_X, STRIP_Y0, STRIP_W, STRIP_Y1 - STRIP_Y0 };
    if (rect_meet(was, (rect_t){ 0, 0, HOME_PANE_X, SCREEN_H }, &part)) clip_set(part), glyphs(); /* over the list */
    if (rect_meet(was, strip, &part)) {
        clip_set(part);
        glyphs_over_art(part, part.w == strip.w && part.h == strip.h);
    }
    clip_set(was);
}
