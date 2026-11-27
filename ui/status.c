/* The status row (docs/oled-design.md#status-row): an overlay on every page with no band and no
 * fill, drawn after the page so its glyphs stand over whatever runs under them. At the left the
 * clock, Inter Medium 15 from x 62, its figures on rows 17 to 27, in Text Secondary, and in Text
 * Primary on Home. At the right, right to left, each only while it has something to say: the
 * battery, its nub ending 62 px from the edge; the play state; Wi-Fi's fan.
 *
 * Where each comes from, all read here with no help from demo: the battery from the kernel's
 * power supply files; the play state from what plays (now.c); Wi-Fi from wlan0's state and its
 * line in /proc/net/wireless. They are read when the minute turns and when what plays changes,
 * never on a timer of their own, so the row costs nothing at rest; a cable put in shows within
 * the minute. ponytail: the output's glyph (wired headphones, the deaf ear with none), Bluetooth,
 * the lock, sync and offline marks are not here: each needs a state that demo alone holds today
 * (docs/ui-rewrite.md#parity). */
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
           *status_wireless = "/proc/net/wireless";
static struct {
    char clock[8];
    run_t run;
    int fixed;
    int level, cable; /* the battery's percent, -1 unknown; 1 on a cable that charges or holds it */
    int wifi;         /* 0 not connected, 1 to 3 the fan's lit parts */
    int play;         /* 0 stopped, 1 playing, 2 paused */
    unsigned seen;    /* now_seq() when play was read */
} S = { .level = -1 };

/* The clock reads hhmm whatever the time: a script's, so a picture is the same on every run. */
void status_clock_fixed(const char *hhmm) {
    snprintf(S.clock, sizeof S.clock, "%s", hhmm);
    S.fixed = 1, S.run.n = 0;
}

rect_t status_rect(void) { return (rect_t){ INSET, CLOCK_Y, SCREEN_W - 2 * INSET, 18 }; }
static rect_t glyphs_rect(void) { return (rect_t){ RIGHT - 80, CLOCK_Y, 80, 18 }; }

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

void status_step(void) {
    char s[8];
    int play = now_get()->playing ? 1 : now_get()->paused ? 2 : 0;
    static int read_once;
    if (play != S.play) S.play = play, ui_mark(glyphs_rect());
    if (!read_once) read_once = 1, read_glyphs();
    if (S.fixed) return;
    time_t t = time(0);
    struct tm *tm = localtime(&t);
    snprintf(s, sizeof s, "%02d:%02d", tm->tm_hour, tm->tm_min);
    if (!strcmp(s, S.clock)) return;
    memcpy(S.clock, s, sizeof S.clock);
    S.run.n = 0;
    ui_mark((rect_t){ INSET, CLOCK_Y, CLOCK_W, 18 });
    read_glyphs(); /* the minute turned: the loop is awake anyway */
}

/* Wi-Fi's fan: a quarter circle standing on its point, cut into a dot and two bands, the lit ones
 * in ink and the rest in the gray a control that is off has. Drawn a pixel at a time by how far it
 * is from the point and how far off upright, its edges smoothed over a pixel. */
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
                uint32_t *p = canvas + y * SCREEN_W + x, rgb = k < lit ? ink : tok(TOK_OFF);
                *p = 0xff000000 | rgb_mix(*p & 0xffffff, rgb, cov);
            }
        }
}

void status_draw(rect_t r) {
    if (!rect_meet(r, status_rect(), 0)) return;
    int home = !strcmp(ui_top_name(), "home");
    uint32_t ink = tok(TOK_PRIMARY);
    if (!S.run.n || run_stale(&S.run)) run_make(&S.run, FACE_MEDIUM, TYPE(15), S.clock, -1);
    /* on Home the clock stands over the list in Text Primary (docs/oled-design.md#status-row) */
    run_draw(&S.run, INSET, CLOCK_Y, tok(home ? TOK_PRIMARY : TOK_SECONDARY), 256);
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
        x = floorf(body.x) - GAP; /* the glyphs left of it on whole pixels: their strokes are 2 and 3 px */
    }
    if (S.play == 1) { /* three level bars standing on the digits' baseline */
        static const float high[] = { 7, 11, 5 };
        for (int k = 0; k < 3; ++k) fill_round((rectf_t){ x - 10 + (float)k * 4, 28 - high[k], 2, high[k] }, 1, 0, ink, 1, 0);
        x -= 10 + GAP;
    } else if (S.play == 2) { /* the pause: two 3 px bars */
        fill_round((rectf_t){ x - 10, 17, 3, 11 }, 0.9f, 0, ink, 1, 0);
        fill_round((rectf_t){ x - 4, 17, 3, 11 }, 0.9f, 0, ink, 1, 0);
        x -= 10 + GAP;
    }
    if (S.wifi) fan(x, 28, S.wifi, ink);
}
