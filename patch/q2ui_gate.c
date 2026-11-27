/* The screen's hand-over to q2ui (docs/ui-rewrite.md#cutover), demo's side: transition scaffolding,
 * here until q2ui is the only UI. While /mnt/data/ui-target says q2ui and the image has
 * /usr/bin/q2ui, demo starts it as its child and runs on under it as the owner of every service
 * (the player, Bluetooth, Wi-Fi, USB, power), painting nothing (ringnav_wm_paint) and leaving the
 * wheel, Centre, Return and touches to it (ringnav_input), as it does for q2video and a card game.
 * Play/Pause and the side buttons stay demo's, so the music's keys work as they do on any page.
 *
 *  - q2ui ends by itself (its Old Menus row): demo has the screen again, whole, and keeps it
 *    until the next start of demo.
 *  - q2ui is ended by a signal (a kill from a shell, a fault): it is started again, unless it had
 *    run under Q2UI_RAN_MS, when demo keeps the screen rather than loop.
 *  - demo needs the screen (a window other than Home or Now Playing opened by itself: the USB
 *    prompt, a pairing request, Shut Down?): q2ui is ended so the window can be seen and
 *    answered, and started again once Home is back on top. Now Playing is not such a window:
 *    stock opens it when a song is chosen, and q2ui has its own over it. Nor is the volume's
 *    display, which every volume step brings up for a moment.
 *  - q2ui ends with Q2UI_RELOAD (kill -HUP sends it there): it is started again on the next pass,
 *    with whatever /mnt/data/q2ui.args now says. No frame of demo's shows between.
 *  - q2ui asks for a page of demo's it has not got: it names the page in Q2UI_ASK and ends with
 *    Q2UI_ASKED. demo opens that page, and starts q2ui again when the page has been left.
 *  - while q2ui runs, demo says what only it knows for q2ui's status row (where the sound goes,
 *    Bluetooth, Buttons Lock, a card app's sync and server: status_row_state) and Control Center
 *    (its switches' states: q2ui_cc_state) in Q2UI_STATUS, a look every Q2UI_STATUS_MS and a
 *    write only when something in it changed.
 *
 * The target is read once, before demo's first paint, so no frame of demo's shows first. With no
 * such file, or no q2ui in the image (any default build), none of this does anything. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

int coverflow_opening(void); /* coverflow.c: Cover Flow is loading before its page comes up */

#define Q2UI_BIN "/usr/bin/q2ui"
#define Q2UI_TARGET "/mnt/data/ui-target"
#define Q2UI_ASK "/tmp/q2ui.open"
#define Q2UI_ASKED 3     /* its exit code with a page named in Q2UI_ASK (ui/q2ui.h UI_EXIT_ASK) */
#define Q2UI_RELOAD 4    /* and to be started again at once (UI_EXIT_RELOAD: kill -HUP, for new options) */
#define Q2UI_OPEN_MS 1500 /* a page asked for that has not come up by now is not coming */
#define Q2UI_RAN_MS 10000 /* a q2ui a signal ended is started again if it had run this long */
#define Q2UI_DUE (-1) /* to be started on the next pass: already the screen's holder */
#define Q2UI_STATUS "/tmp/q2ui.status" /* ui/status.c reads it when it changes (inotify) */
#define Q2UI_STATUS_MS 500

unsigned status_row_state(char *b, unsigned room);  /* status_row.c */
unsigned q2ui_cc_state(char *b, unsigned room);     /* control_center.c: q2ui's Control Center's */

static struct {
    int pid;           /* q2ui's, Q2UI_DUE, or 0 */
    char asked;        /* the target was read at start */
    char took, back;   /* demo ended it for a window of its own; start it again on Home */
    char booted;       /* it has been started once: the boot animation is over */
    char opening;      /* a page q2ui asked for is on its way up: Home on top does not yet mean it was left */
    unsigned started_at; /* time_now_ms when q2ui was last started */
    unsigned asked_at; /* time_now_ms when it was asked for */
    unsigned told_at, told; /* the status's last look (time_now_ms), and the hash of what was written */
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

/* Nothing of demo's on top that the user has to see or answer: Home, Now Playing, the volume's
 * own display (it comes up at every volume step and goes by itself), the USB Debugging page
 * (usb_ui.c: it stands on top from boot for as long as the cable is in with debugging on, and
 * only says so; with it counted, a Q2 on a developer's cable never reached q2ui), or no window yet
 * (demo still starting) */
static int on_home(void) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", "") : "home_page";
    return !tk_strcmp(name, "home_page") || !tk_strcmp(name, "playing_page") || !tk_strcmp(name, "volume_dialog") ||
           !tk_strcmp(name, "usbdebug_page");
}

/* A line in demo's log for each thing the gate does: with none, a Q2 that came up on demo's menus
 * said nothing of why. */
void ulog(char level, const char *src, const char *msg);
static void said(const char *what, int n) {
    char line[64];
    tk_snprintf(line, sizeof line, "%s %d", what, n);
    ulog('I', "q2ui", line);
}

/* demo's pages that are built by an opener of the payload's, not by a stock page's name: each is
 * the click of the Home or Library row that leads to it, and takes nothing from the click. */
int tuner_open(void *ctx, void *event), games_open(void *ctx, void *event), era_menu(void *ctx, void *event);
int ratings_menu(void *ctx, void *event), most_played(void *ctx, void *event);
int library_search_open(void *ctx, void *event), coverflow_row_open(void *ctx, void *event);
int carremote_open(void *ctx, void *event), controls_open(void *ctx, void *event), usb_pick_open(void *ctx, void *event);

static const struct {
    const char *name;
    int (*open)(void *ctx, void *event);
} openers[] = { { "tuner_page", tuner_open },     { "games_page", games_open },         { "era_page", era_menu },
                { "ratings_page", ratings_menu }, { "mostplayed_page", most_played },
                { "searchstrip_page", library_search_open }, { "coverflow_page", coverflow_row_open },
                { "carremote_page", carremote_open }, { "controls_page", controls_open },
                { "usbpick_page", usb_pick_open } };

/* The page q2ui named as it left, opened: one of the openers' names, else a stock page's name and
 * nothing else (lower case and underscores ending _page, after at most one folder of lower case
 * letters and a slash: systemset/wifi_page), since what is read here is a file any program can
 * write. */
static void open_asked(void) {
    char name[40] = "";
    int fd = open(Q2UI_ASK, 0);
    if (fd < 0) return;
    int n = (int)read(fd, name, sizeof name - 1), ok = n > 5;
    close(fd);
    while (n > 0 && (name[n - 1] == '\n' || name[n - 1] == '\r')) name[--n] = 0;
    int slash = -1;
    for (int i = 0; i < n; ++i) {
        if (name[i] == '/' && slash < 0 && i > 0) {
            slash = i;
            continue;
        }
        ok &= (name[i] >= 'a' && name[i] <= 'z') || name[i] == '_';
    }
    for (int i = 0; i < slash; ++i) ok &= name[i] >= 'a' && name[i] <= 'z'; /* the folder: letters alone */
    ok &= n - slash - 1 > 5; /* a page's own name after it */
    if (!ok || n < 6 || memcmp(name + n - 5, "_page", 5)) return;
    unsigned k = 0;
    while (k < sizeof openers / sizeof *openers && tk_strcmp(name, openers[k].name)) ++k;
    if (k < sizeof openers / sizeof *openers)
        openers[k].open((void *)0, (void *)0);
    else
        navigator_to(name);
    ulog('I', "q2ui", name);
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
    qu.started_at = (unsigned)time_now_ms();
    said("started, pid", qu.pid);
    if (!qu.pid) widget_invalidate_force(window_manager(), 0);
}

/* The status row's states for q2ui (Q2UI_STATUS), written when they change; looked at every
 * Q2UI_STATUS_MS, so a cable put in shows in q2ui's row within half a second. */
static void tell(void) {
    unsigned now = (unsigned)time_now_ms();
    if (qu.told_at && now - qu.told_at < Q2UI_STATUS_MS) return;
    qu.told_at = now | !now;
    char b[320];
    unsigned n = status_row_state(b, sizeof b);
    n += q2ui_cc_state(b + n, sizeof b - n);
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)b, n);
    if (h != qu.told && blob_io(Q2UI_STATUS, Q2UI_STATUS ".tmp", b, n, 1)) qu.told = h;
}

/* every UI loop pass */
void q2ui_poll(void) {
    if (!q2ui_on()) {
        if (!qu.back) return;
        if (qu.opening) { /* the page asked for comes up a pass or more after it is asked for */
            /* Cover Flow may load for seconds before its page comes up: it is still on its way */
            if (!on_home() || (!coverflow_opening() && (unsigned)time_now_ms() - qu.asked_at >= Q2UI_OPEN_MS)) qu.opening = 0;
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
        unsigned ran = (unsigned)time_now_ms() - qu.started_at;
        said(qu.took ? "ended for a window of demo's, status" : "ended, status", status);
        qu.pid = 0;
        qu.back = qu.took;
        /* ended by a signal that was not the gate's (a kill from a shell, a fault): started again,
         * unless it had only just started, which is a q2ui that cannot run and would loop */
        if (!qu.took && (status & 0x7f) && ran >= Q2UI_RAN_MS) qu.pid = Q2UI_DUE;
        if (!qu.took && status == Q2UI_ASKED << 8) open_asked();
        if (!qu.took && status == Q2UI_RELOAD << 8) qu.pid = Q2UI_DUE;
        widget_invalidate_force(window_manager(), 0);
        return;
    }
    tell();
    if (!qu.took && !on_home()) {
        char cmd[24];
        tk_snprintf(cmd, sizeof cmd, "kill %d", qu.pid); /* demo imports no kill() */
        system(cmd);
        qu.took = 1;
        ulog('I', "q2ui", widget_get_prop_str(window_manager_get_top_window(window_manager()), "name", "a window"));
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
