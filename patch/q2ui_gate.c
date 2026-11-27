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
 *  - q2ui asks demo to change a setting (Control Center's switches, its Display page's choices) with a
 *    line "set NAME VALUE" down a pipe of its own (ctl): demo makes it once, before the first
 *    start, and hands q2ui its write end, the fd's number after --ctl, so no other program, the
 *    phone's daemon among them, can write it. Car Remote's queue takes no "set". Its clicks come
 *    the same way ("click 0|1"), as demo's own wheel and keys would sound them, and its Wi-Fi
 *    page's search ("scan") and join of a network by its name ("join NAME"), whose networks demo
 *    tells it in Q2UI_WIFI (wifi_flows.c), and its Bluetooth page's scan ("btscan"), connect or
 *    disconnect ("btlink MAC") and forget ("btforget MAC") of a device, told in Q2UI_BT (bt_ui.c).
 *  - while q2ui runs, demo says what only it knows for q2ui's status row (where the sound goes,
 *    Bluetooth, Buttons Lock, a card app's sync and server: status_row_state) and Control Center
 *    (its switches' states: q2ui_cc_state) in Q2UI_STATUS, a look every Q2UI_STATUS_MS and a
 *    write only when something in it changed. It names the playing song's cover file there too
 *    (cover=, song_cover_url), as the player writes an embedded picture and a folder image or a
 *    download to two files and leaves the song before's behind (docs/ui-rewrite.md#cutover).
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
#define Q2UI_CTL_LINE 80 /* a line down the pipe, its '\n' included (a join's: "join ", a network's 32 bytes each escaped, '\n'); a longer one is dropped */
#define Q2UI_WIFI "/tmp/q2ui.wifi" /* the networks for q2ui's Wi-Fi page (wifi_flows.c wifi_flows_networks) */
#define Q2UI_WIFI_ROOM 2048
#define Q2UI_BT "/tmp/q2ui.bt" /* the devices for q2ui's Bluetooth page (bt_ui.c bt_q2ui_devices) */

int chapter_pipe2(int *p, int flags); /* chapters.c: demo imports no pipe() */
int fcntl(int fd, int cmd, ...);
#define CTL_NONBLOCK 0x80   /* O_NONBLOCK, MIPS */
#define CTL_CLOEXEC 0x80000 /* O_CLOEXEC */
#define CTL_SETFD 2         /* F_SETFD */
#define CTL_READS 4

unsigned status_row_state(char *b, unsigned room);  /* status_row.c */
int wifi_flows_scan(void), wifi_flows_join(const char *ssid); /* wifi_flows.c: q2ui's Wi-Fi page's */
unsigned wifi_flows_networks(char *b, unsigned room);
int bt_q2ui_scan(void), bt_q2ui_link(const char *mac), bt_q2ui_forget(const char *mac); /* bt_ui.c: its Bluetooth page's */
unsigned bt_q2ui_devices(char *b, unsigned room);
int bt_mac_ok(const char *s);
unsigned q2ui_cc_state(char *b, unsigned room);     /* control_center.c: q2ui's Control Center's */
void *queue_now(unsigned *pos, unsigned *n);       /* coverflow.c */
int song_cover_url(void *r, char *url, unsigned size); /* coverflow.c: a file:// URL, 0 for none */
void vamp_version(char *v, unsigned size);            /* nav/chapters_streams_folders.c: About's */

static struct {
    int pid;           /* q2ui's, Q2UI_DUE, or 0 */
    char asked;        /* the target was read at start */
    char took, back;   /* demo ended it for a window of its own; start it again on Home */
    char booted;       /* it has been started once: the boot animation is over */
    char opening;      /* a page q2ui asked for is on its way up: Home on top does not yet mean it was left */
    unsigned started_at; /* time_now_ms when q2ui was last started */
    unsigned asked_at; /* time_now_ms when it was asked for */
    unsigned told_at, told; /* the status's last look (time_now_ms), and the hash of what was written */
    unsigned told_wifi, told_bt; /* the same for Q2UI_WIFI and Q2UI_BT */
    char piped;        /* ctl holds the pipe's ends: read (demo's), write (q2ui's) */
    int ctl[2];
    unsigned carry;    /* bytes of a line not yet ended, at the start of line */
    char line[Q2UI_CTL_LINE];
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
    /* q2ui's pipe for settings: both ends close in every other child demo starts (O_CLOEXEC), and
     * demo's end never waits (O_NONBLOCK) */
    if (!qu.piped && !chapter_pipe2(qu.ctl, CTL_NONBLOCK | CTL_CLOEXEC)) qu.piped = 1;
    char fd[12];
    tk_snprintf(fd, sizeof fd, "%d", qu.ctl[1]);
    int pid = fork();
    if (!pid) {
        child_oom_first();
        /* the write end stays open across the exec, in q2ui alone */
        if (qu.piped && !fcntl(qu.ctl[1], CTL_SETFD, 0))
            execl(Q2UI_BIN, Q2UI_BIN, "--ctl", fd, (char *)0);
        else
            execl(Q2UI_BIN, Q2UI_BIN, (char *)0);
        child_exit(127);
    }
    qu.pid = pid > 0 ? pid : 0;
    qu.took = 0;
    qu.started_at = (unsigned)time_now_ms();
    said("started, pid", qu.pid);
    if (!qu.pid) widget_invalidate_force(window_manager(), 0);
}

/* The networks for q2ui's Wi-Fi page and the devices for its Bluetooth page, each written to a file
 * of its own when it changes (the status's room is too small for a scan's names), on tell's looks. */
static void tell_list(const char *path, const char *tmp, unsigned (*list)(char *, unsigned), unsigned *told) {
    char b[Q2UI_WIFI_ROOM];
    unsigned n = list(b, sizeof b), h = hash_bytes(FNV_SEED, (const unsigned char *)b, n);
    if (h != *told && blob_io(path, tmp, b, n, 1)) *told = h;
}
static void tell_networks(void) {
    tell_list(Q2UI_WIFI, Q2UI_WIFI ".tmp", wifi_flows_networks, &qu.told_wifi);
    tell_list(Q2UI_BT, Q2UI_BT ".tmp", bt_q2ui_devices, &qu.told_bt);
}

/* The status row's states for q2ui (Q2UI_STATUS), written when they change; looked at every
 * Q2UI_STATUS_MS, so a cable put in shows in q2ui's row within half a second. Then About's
 * product=, version= and firmware= (this build's, as demo's About shows them), and last, cover=, the
 * playing song's cover's file or nothing (ui/now.c now_cover reads it): its bytes change with every
 * song, which q2ui sees by its watch of the file's folder, so only a change of file is written here.
 * ponytail: song_cover_url's stat of Cover Flow's copy and access of a card app's each look while
 * q2ui runs; a key of the queue's song and the player's type would spare them. */
static void tell(void) {
    unsigned now = (unsigned)time_now_ms();
    if (qu.told_at && now - qu.told_at < Q2UI_STATUS_MS) return;
    qu.told_at = now | !now;
    char b[1024], url[600];
    unsigned pos, count;
    unsigned n = status_row_state(b, 320);
    n += q2ui_cc_state(b + n, 320 - n);
    if (!song_cover_url(queue_now(&pos, &count), url, sizeof url)) url[7] = 0; /* past "file://": none */
    char v[64];
    vamp_version(v, sizeof v);
    int k = tk_snprintf(b + n, sizeof b - n, "product=%s\nversion=%s\nfirmware=%s\ncover=%s\n", PRODUCT_NAME, v,
                        STOCK_VERSION, url + 7);
    if (k > 0 && (unsigned)k < sizeof b - n) n += (unsigned)k;
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)b, n);
    if (h != qu.told && blob_io(Q2UI_STATUS, Q2UI_STATUS ".tmp", b, n, 1)) qu.told = h;
    tell_networks();
}

/* A join's network as q2ui sent it, its \\ and \n undone, into out (SSID_ROOM). */
#define SSID_ROOM 33
static void unescape(const char *s, char *out) {
    unsigned n = 0;
    for (; *s && n < SSID_ROOM - 1; ++s) {
        if (*s == '\\' && s[1]) ++s, out[n++] = *s == 'n' ? '\n' : *s;
        else out[n++] = *s;
    }
    out[n] = 0;
}

/* q2ui's lines down its pipe, each "set NAME VALUE" (control_center.c q2ui_cc_set), acted on in the
 * order sent, or "click 0" (a wheel tick's) or "click 1" (a press's), demo's key click for the wheel
 * and keys q2ui reads itself, at most one of each a pass (the buzzer is busy for a click's length
 * anyway), or its Wi-Fi page's "scan" or "join NAME" (wifi_flows.c), or its Bluetooth page's
 * "btscan", "btlink MAC" or "btforget MAC" (bt_ui.c); another verb, a MAC that is not one, or a line
 * too long, is dropped. Read every pass while it runs and after it
 * ends, so a setting sent just before leaving for a page of demo's is not lost; at most CTL_READS
 * reads a pass, so a q2ui that writes without end cannot hold demo's loop (the rest waits in the pipe). */
int q2ui_cc_set(const char *name, int on); /* control_center.c */
void q2ui_cc_poll(void);
extern int clicker_kind;    /* clicker.c: navigation.h CLICK_KIND_*, 0 the wheel's */
#define CLICK_BUTTON 2      /* CLICK_KIND_BUTTON */
static void ctl(void) {
    char word[12], word2[18]; /* a setting's name; a device's MAC */
    int v, clicks = 0; /* bit 0 a wheel's click asked for this pass, bit 1 a press's */
    for (int k = 0; k < CTL_READS; ++k) {
        int n = (int)read(qu.ctl[0], qu.line + qu.carry, sizeof qu.line - qu.carry);
        if (n <= 0) break; /* nothing waiting (EAGAIN) */
        unsigned end = qu.carry + (unsigned)n, from = 0;
        for (unsigned i = 0; i < end; ++i) {
            if (qu.line[i] != '\n') continue;
            qu.line[i] = 0;
            if (__isoc99_sscanf(qu.line + from, "set %11s %d", word, &v) == 2) {
                char what[32];
                tk_snprintf(what, sizeof what, q2ui_cc_set(word, v) ? "set %s refused," : "set %s", word);
                said(what, v);
            } else if (__isoc99_sscanf(qu.line + from, "click %d", &v) == 1)
                clicks |= v ? 2 : 1;
            else if (!tk_strcmp(qu.line + from, "scan"))
                said(wifi_flows_scan() ? "scan refused," : "scan", 0);
            else if (!memcmp(qu.line + from, "join ", 5)) { /* the network's name is the user's: not logged */
                char ssid[SSID_ROOM];
                unescape(qu.line + from + 5, ssid);
                said(wifi_flows_join(ssid) ? "join refused," : "join", 0);
            } else if (!tk_strcmp(qu.line + from, "btscan"))
                said(bt_q2ui_scan() ? "btscan refused," : "btscan", 0);
            else if (__isoc99_sscanf(qu.line + from, "btlink %17s", word2) == 1 && bt_mac_ok(word2))
                said(bt_q2ui_link(word2) ? "btlink refused," : "btlink", 0);
            else if (__isoc99_sscanf(qu.line + from, "btforget %17s", word2) == 1 && bt_mac_ok(word2))
                said(bt_q2ui_forget(word2) ? "btforget refused," : "btforget", 0);
            from = i + 1;
        }
        qu.carry = end - from;
        if (qu.carry == sizeof qu.line) qu.carry = 0; /* no line is this long: dropped */
        if (from && qu.carry) memmove(qu.line, qu.line + from, qu.carry);
    }
    /* each as demo's own wheel or key would click it: Key Tone and the Clicker's route are
     * ringnav_buzzer's (docs/internals.md#key-tone) */
    if (clicks & 1) buzzeer_switch(1);
    if (clicks & 2) clicker_kind = CLICK_BUTTON, buzzeer_switch(1), clicker_kind = 0;
}

/* every UI loop pass */
void q2ui_poll(void) {
    if (qu.piped) ctl(), q2ui_cc_poll(); /* a Bluetooth flip kept while its stack started */
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
