/* The screen's hand-over to q2ui (docs/ui-rewrite.md#cutover), demo's side: transition scaffolding,
 * here until q2ui is the only UI. While /mnt/data/ui-target says q2ui and the image has
 * /usr/bin/q2ui, demo starts it as its child and runs on under it as the owner of every service
 * (the player, Bluetooth, Wi-Fi, USB, power), painting nothing (ringnav_wm_paint) and leaving the
 * wheel, Centre, Return and touches to it (ringnav_input), as it does for q2video and a card game.
 * Play/Pause and the side buttons stay demo's, so the music's keys work as they do on any page.
 *
 *  - q2ui ends (Return on its first page, or a crash): demo has the screen again, whole, and
 *    keeps it until the next start of demo. Return on q2ui's first page is the way out of q2ui.
 *  - demo needs the screen (a window other than Home or Now Playing opened by itself: the USB
 *    prompt, a pairing request, Shut Down?): q2ui is ended so the window can be seen and
 *    answered, and started again once Home is back on top. Now Playing is not such a window:
 *    stock opens it when a song is chosen, and q2ui has its own over it.
 *  - q2ui asks for a page of demo's it has not got: it names the page in Q2UI_ASK and ends with
 *    Q2UI_ASKED. demo opens that page, and starts q2ui again when the page has been left.
 *
 * The target is read once, before demo's first paint, so no frame of demo's shows first. With no
 * such file, or no q2ui in the image (any default build), none of this does anything. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define Q2UI_BIN "/usr/bin/q2ui"
#define Q2UI_TARGET "/mnt/data/ui-target"
#define Q2UI_ASK "/tmp/q2ui.open"
#define Q2UI_ASKED 3     /* its exit code with a page named in Q2UI_ASK (ui/main.c ui_demo_page) */
#define Q2UI_OPEN_MS 1500 /* a page asked for that has not come up by now is not coming */
#define Q2UI_DUE (-1) /* to be started on the next pass: already the screen's holder */

static struct {
    int pid;           /* q2ui's, Q2UI_DUE, or 0 */
    char asked;        /* the target was read at start */
    char took, back;   /* demo ended it for a window of its own; start it again on Home */
    char booted;       /* it has been started once: the boot animation is over */
    char opening;      /* a page q2ui asked for is on its way up: Home on top does not yet mean it was left */
    unsigned asked_at; /* time_now_ms when it was asked for */
} qu __attribute__((section(".scratch")));

static int wanted(void) {
    char word[8] = "";
    int fd = open(Q2UI_TARGET, 0);
    if (fd < 0) return 0;
    int n = (int)read(fd, word, 4);
    close(fd);
    return n == 4 && !memcmp(word, "q2ui", 4) && !access(Q2UI_BIN, 1);
}

int q2ui_on(void) {
    if (!qu.asked) {
        qu.asked = 1;
        if (wanted()) qu.pid = Q2UI_DUE;
    }
    return qu.pid;
}

/* Nothing of demo's on top that the user has to see: Home, Now Playing, or no window yet (demo
 * still starting) */
static int on_home(void) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", "") : "home_page";
    return !tk_strcmp(name, "home_page") || !tk_strcmp(name, "playing_page");
}

/* The page q2ui named as it left, opened: a stock page's name and nothing else (lower case and
 * underscores, ending _page), since what is read here is a file any program can write. */
static void open_asked(void) {
    char name[40] = "";
    int fd = open(Q2UI_ASK, 0);
    if (fd < 0) return;
    int n = (int)read(fd, name, sizeof name - 1), ok = n > 5;
    close(fd);
    while (n > 0 && (name[n - 1] == '\n' || name[n - 1] == '\r')) name[--n] = 0;
    for (int i = 0; i < n; ++i) ok &= (name[i] >= 'a' && name[i] <= 'z') || name[i] == '_';
    if (!ok || n < 6 || memcmp(name + n - 5, "_page", 5)) return;
    navigator_to(name);
    qu.back = 1, qu.opening = 1, qu.asked_at = (unsigned)time_now_ms();
}

static void start(void) {
    /* the boot animation draws on the page on screen until demo shows a frame, which it will not
     * while q2ui has the screen (splash_hold.c): it ends here, as before Rockbox */
    if (!qu.booted) system("killall q2splash 2>/dev/null");
    qu.booted = 1;
    int pid = fork();
    if (!pid) {
        child_oom_first();
        execl(Q2UI_BIN, Q2UI_BIN, (char *)0);
        child_exit(127);
    }
    qu.pid = pid > 0 ? pid : 0;
    qu.took = 0;
    if (!qu.pid) widget_invalidate_force(window_manager(), 0);
}

/* every UI loop pass */
void q2ui_poll(void) {
    if (!q2ui_on()) {
        if (!qu.back) return;
        if (qu.opening) { /* the page asked for comes up a pass or more after it is asked for */
            if (!on_home() || (unsigned)time_now_ms() - qu.asked_at >= Q2UI_OPEN_MS) qu.opening = 0;
            return;
        }
        if (!on_home()) return;
        qu.back = 0, qu.pid = Q2UI_DUE; /* the window demo had the screen for has closed */
    }
    if (qu.pid == Q2UI_DUE) {
        start();
        return;
    }
    int status = 0;
    if (waitpid(qu.pid, &status, 1)) { /* WNOHANG: it has ended */
        qu.pid = 0;
        qu.back = qu.took;
        if (!qu.took && status == Q2UI_ASKED << 8) open_asked();
        widget_invalidate_force(window_manager(), 0);
        return;
    }
    if (!qu.took && !on_home()) {
        char cmd[24];
        tk_snprintf(cmd, sizeof cmd, "kill %d", qu.pid); /* demo imports no kill() */
        system(cmd);
        qu.took = 1;
    }
}

/* ringnav_input's, for an event of type and key: 1 when it is q2ui's, which reads the wheel, Centre,
 * Return and the touch panel itself; the other keys stay demo's. */
int q2ui_input(unsigned type, unsigned key) {
    if (!q2ui_on()) return 0;
    int press = type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG;
    int its = (type >= EVT_POINTER_FIRST && type <= EVT_POINTER_ABORT) ||
              (press && (key == KEY_PREV || key == KEY_NEXT || key == KEY_CENTER || key == KEY_RETURN));
    if (its) reset_poweroptions_timer(1, 1, 1); /* the screen stays lit while it is used */
    return its;
}
