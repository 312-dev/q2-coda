/* The status row (docs/oled-design.md#status-row): an overlay on every page with no band and no
 * fill, drawn after the page so its glyphs stand over whatever runs under them. The clock is
 * Inter Medium 15 from x 62, its figures on rows 17 to 27, in Text Secondary, and in Text Primary
 * on Home. */
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "q2ui.h"

#define INSET 62
#define CLOCK_Y 14 /* the 18 px line box whose figures stand on rows 17 to 27 */
#define CLOCK_W 64

static struct {
    char clock[8];
    run_t run;
    int fixed;
} S;

/* The clock reads hhmm whatever the time: a script's, so a picture is the same on every run. */
void status_clock_fixed(const char *hhmm) {
    snprintf(S.clock, sizeof S.clock, "%s", hhmm);
    S.fixed = 1, S.run.n = 0;
}

rect_t status_rect(void) { return (rect_t){ INSET, CLOCK_Y, SCREEN_W - 2 * INSET, 18 }; }

void status_step(void) {
    char s[8];
    if (S.fixed) return;
    time_t t = time(0);
    struct tm *tm = localtime(&t);
    snprintf(s, sizeof s, "%02d:%02d", tm->tm_hour, tm->tm_min);
    if (!strcmp(s, S.clock)) return;
    memcpy(S.clock, s, sizeof S.clock);
    S.run.n = 0;
    ui_mark((rect_t){ INSET, CLOCK_Y, CLOCK_W, 18 });
}

void status_draw(rect_t r) {
    if (!rect_meet(r, status_rect(), 0)) return;
    if (!S.run.n || run_stale(&S.run)) run_make(&S.run, FACE_MEDIUM, TYPE(15), S.clock, -1);
    /* on Home the clock stands over the list in Text Primary (docs/oled-design.md#status-row) */
    run_draw(&S.run, INSET, CLOCK_Y, tok(strcmp(ui_top_name(), "home") ? TOK_SECONDARY : TOK_PRIMARY), 256);
}
