/* Software Update (docs/internals.md#sd-card-update, docs/oled-design.md#software-update):
 * General's Software Update row (sw_update_open) checks online and the SD card at once and offers
 * what it finds, the internet's first; the card's alone (the online download done, or stock's own
 * TF row) runs fwupdate_tf (0x4c7eb4), hooked whole here. Stock asked first, then checked, then
 * flashed; here the card's update.tar is checked first, on a thread, while a page says so, and only
 * an update stock's updater would take is offered:
 *
 * - stock's own checks first (check_updatefw_exist, on the UI thread as stock's): the battery
 * (under 16% with no cable in: Charge to Update), the card, the file, and room for its unpacking
 * (No SD Card, No Update Found, Not Enough Space);
 * - then Checking for an Update... (the page's spinner) while sw_check reads the tar as
 * update_firmware (0x5028e0) will: firmware_v20.info first, "Shanling Q2" and a version holding a V
 * (else Update Can't Be Used), the version not this build's (else You're Up to Date: stock refuses
 * an equal one), and the MD5s its third and fourth lines give for recovery-update/xImage and
 * rootfs.squashfs, read from the tar as they stream by (else Can't Be Used);
 * - beside it, with Wi-Fi on, the online check on a thread of its own (sw_net,
 * netupdate.c netupdate_check): the latest release's tag and size. Either check failing never
 * holds the other up; sw_offer weighs the two answers;
 * - then the offer: the update's name, its source and size and what is installed, with Download and
 * Install (online) or Install (the card), and under it at most one line the wheel reaches: Install
 * from SD Card instead (both have one), Turn on Wi-Fi to check online, or Couldn't check online ·
 * Try Again;
 * - Download and Install fetches it to the card (netupdate.c) with its progress on the page, then
 * checks the card's file as above; Install asks on the confirmation card, Install
 * SW_PRODUCT 1.8.3?: the update's version, this build's tag and the file's size, Cancel (selected)
 * and Install and Restart, which runs stock's checkfw_dialog as stock's fwupdate_tf did: its thread
 * unpacks and checks the tar again, marks the next boot as the recovery's and restarts, and the
 * recovery flashes. That dialog is drawn as a mode page here (Restarting to Install), with the
 * unpacking's progress; should its own check fail after all, the dialog's answer opens the page it
 * means.
 *
 * --dev builds also take q2update's requests (q2update_poll,
 * docs/adb.md#installing-an-update-over-adb): the card's path started from a computer over adb,
 * with no Install Update? card. */
#include "navigation.h"

#define SW_CARD "/mnt/mmc"
#define SW_FILE SW_CARD "/update.tar"
#define SW_PARTS                                                                                   \
    SW_CARD "/recovery-update/" /* where stock's tar unpacks them: the progress bar's measure */
#define SW_BATTERY 16           /* check_updatefw_exist's least level with no cable in */
#define SW_INFO_MAX 1024        /* firmware_v20.info: two lines and two MD5 lines */
#define SW_CHUNK (64 << 10)
#define SW_SPIN_MS 80 /* a frame of the twelve: a turn a second */
#define SW_SPIN_PX 48 /* tools/art.py SW_SPIN */
enum {
    SW_CHECKING,
    SW_OK,
    SW_LATEST,
    SW_NONE,
    SW_NOCARD,
    SW_LOW,
    SW_BAD,
    SW_FULL,
    SW_CANCELLED,
    SW_ASKING,     /* swu.state: OK taken, Install Update? asked on the card */
    SW_OFFER_NET,  /* swu.result: the online update offered */
    SW_OFFER_SD,   /* ... the card's */
    SW_DOWNLOADING /* ... the online one coming to the card */
};
/* swu.online: no online part (the card's alone), Wi-Fi off, checking, then netupdate_check's */
enum { ON_NONE = -2, ON_OFF = -1, ON_CHECKING = 0 };
/* swu.note: the line under the offer's button or a result */
enum { NOTE_NONE, NOTE_SD, NOTE_WIFI, NOTE_RETRY };
/* stock's answers: check_updatefw_exist's, and checkfw_dialog's dialog_quit codes (fwupdate_tf's
 * own tests) */
enum { STOCK_FOUND = 1, STOCK_NOCARD = -2, STOCK_NOFILE = -11, STOCK_FULL = -12, STOCK_LOW = -15 };
enum { QUIT_NOFILE = 9, QUIT_LATEST = 13, QUIT_BAD = 18 };

/* The restart's bar (U3): under the stack's line, 180 x 6 with round ends */
#define SW_BAR_Y 172
#define SW_BAR_W 180
#define SW_BAR_H 6
#define SW_STATUS_PX TYPE(15) /* the status row's title, Inter Medium 15 */
/* The product's name in the update's (sw_update_name) */
#define SW_PRODUCT "Q2 Muse"
/* The offer (29 v3): a 60 px accent circle centred on y 92 with its 30 px glyph, the name (Inter
 * Medium 22) on y 142, the source and size (15 px Text Secondary) on 168, the installed version
 * (13 px Text Tertiary) on 188, the button centred on 225 and the line under it (15 px accent
 * text) on 266, screen rows */
#define SW_OFFER_R 30
#define SW_OFFER_CY 92
#define SW_OFFER_GLYPH 30
#define SW_OFFER_NAME_Y 142
#define SW_OFFER_FROM_Y 168
#define SW_OFFER_HAVE_Y 188
#define SW_OFFER_BTN_Y 225
#define SW_OFFER_LINE_Y 266
#define SW_LINK_H 32  /* the line's tap target */
#define SW_NOTE_Y 176 /* a result's line, from its mode page's top: 28 px under its own line */

static struct {
    unsigned long thread;
    volatile int state, cancel; /* state: SW_CHECKING while sw_check runs, then its answer */
    int result, level, frame;
    unsigned timer, size_kb;
    long long parts; /* xImage's and rootfs.squashfs's bytes, what stock's tar unpacks */
    void *page, *restart;
    char tag[24]; /* the update's version line */
    /* the online half: its check's answer (ON_* or NET_*), its thread while it runs, the release's
     * tag and size; the card's check's thread while it runs */
    int online, net_up, sd_up;
    unsigned long net_thread;
    unsigned net_kb;
    char net_tag[32];
    int note, focus, percent, verifying; /* focus: the wheel's, 0 the button, 1 the line */
    rect_t button, link;                 /* their tap targets as last painted, screen rows */
} swu __attribute__((section(".scratch")));

#if Q2POD_DEV
static void sw_remote_end(int state);
static int sw_remote_ok(void);
static void sw_remote_failed(int quit);
#else
#define sw_remote_end(state) ((void)0)
#define sw_remote_ok() 0
#define sw_remote_failed(quit) ((void)0)
#endif

/* ---- the check, on its own thread (sw_check), or from test/fw ---- */

/* The next line of *p into line (cut to n), past it in *p; 0 at the end. */
static int sw_line(char **p, char *line, unsigned n) {
    char *s = *p;
    unsigned len = 0;
    if (!*s) return 0;
    while (s[len] && s[len] != '\n') ++len;
    *p = s + len + (s[len] == '\n');
    if (len >= n) len = n - 1;
    memcpy(line, s, len), line[len] = 0;
    return 1;
}
/* stock's toolsTrimLeft and toolsTrimRight on the first two lines */
static char *sw_trim(char *s) {
    while (*s == ' ' || *s == '\t' || *s == '\r') ++s;
    char *e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = 0;
    return s;
}
static unsigned sw_octal(const unsigned char *h, int n) {
    unsigned v = 0;
    for (int i = 0; i < n && h[i] >= '0' && h[i] <= '7'; ++i) v = v * 8 + (h[i] - '0');
    return v;
}
/* n bytes of f through MD5, as lowercase hex in hex; whether all were there. */
static int sw_md5(void *f, unsigned n, unsigned char *buf, char hex[33]) {
    unsigned ctx[24], got;
    unsigned char md[16];
    MD5_Init(ctx);
    for (; n; n -= got) {
        got = (unsigned)fread(buf, 1, n < SW_CHUNK ? n : SW_CHUNK, f);
        if (!got || swu.cancel) return 0;
        MD5_Update(ctx, buf, got);
    }
    MD5_Final(md, ctx);
    for (int i = 0; i < 16; ++i) tk_snprintf(hex + 2 * i, 3, "%02x", md[i]);
    return 1;
}

/* path's tar as update_firmware will take it: SW_OK, SW_LATEST (the version is this build's),
 * SW_NONE (no file), SW_BAD (another device's, damaged, cut short) or SW_CANCELLED. swu.tag,
 * swu.size_kb and swu.parts as read. */
int sw_check_file(const char *path, const char *installed) {
    unsigned char head[512];
    char info[SW_INFO_MAX + 1], line[4][80], *p = info, hex[33];
    int r = SW_BAD, seen = 0, i = 0;
    void *f = fopen(path, "rb");
    swu.tag[0] = 0, swu.parts = 0, swu.size_kb = 0;
    if (!f) return SW_NONE;
    unsigned char *buf = calloc(1, SW_CHUNK);
    if (!fseek(f, 0, 2)) swu.size_kb = (unsigned)(ftell(f) >> 10);
    fseek(f, 0, 0);
    /* firmware_v20.info, the tar's first member */
    unsigned n = fread(head, 1, 512, f) == 512 ? sw_octal(head + 124, 12) : 0;
    if (!buf || tk_strcmp((char *)head, "firmware_v20.info") || !n || n > SW_INFO_MAX ||
        fread(info, 1, (n + 511) & ~511u, f) != ((n + 511) & ~511u))
        goto done;
    info[n] = 0;
    for (; i < 4 && sw_line(&p, line[i], sizeof *line); ++i) {}
    char *version = i < 4 ? (char *)0 : sw_trim(line[1]);
    if (!version || tk_strcmp(sw_trim(line[0]), "Shanling Q2") || !strrchr(version, 'V')) goto done;
    tk_snprintf(swu.tag, sizeof swu.tag, "%s", version);
    if (!tk_strcmp(version, installed)) {
        r = SW_LATEST;
        goto done;
    }
    /* the rest, member by member: the two images' MD5s against the third and fourth lines */
    while (fread(head, 1, 512, f) == 512 && head[0]) {
        n = sw_octal(head + 124, 12);
        int k = !tk_strcmp((char *)head, "recovery-update/xImage")            ? 2
                : !tk_strcmp((char *)head, "recovery-update/rootfs.squashfs") ? 3
                                                                              : 0;
        if (k) {
            if (!sw_md5(f, n, buf, hex) || strncmp(hex, line[k], 32)) goto done;
            seen |= 1 << k, swu.parts += n;
            if (fseek(f, (long)(((n + 511) & ~511u) - n), 1)) goto done;
        } else if (fseek(f, (long)((n + 511) & ~511u), 1))
            goto done;
    }
    r = seen == 12 ? SW_OK : SW_BAD;
done:
    if (swu.cancel) r = SW_CANCELLED;
    free(buf);
    fclose(f);
    return r;
}

static void *sw_check(void *unused) {
    swu.state = sw_check_file(SW_FILE, Q2POD_FW_TAG);
    return 0;
}
static void *sw_net(void *unused) {
    swu.online = netupdate_check(swu.net_tag, sizeof swu.net_tag, &swu.net_kb);
    return 0;
}

/* What the page shows from the card's answer (sd, SW_*) and the online check's (net, ON_* or
 * NET_*), and in *note the line under it. The two are weighed apart: an online check that failed
 * or never ran leaves the card's answer whole, and says so under it. Low battery stops either
 * install (stock's updater refuses it); the internet's update comes first, the card's then a line
 * away; a card file that can't be used says why; else You're Up to Date. */
int sw_offer(int sd, int net, int *note) {
    *note = NOTE_NONE;
    if (sd == SW_LOW) return SW_LOW;
    if (net == NET_NEWER) {
        if (sd == SW_OK) *note = NOTE_SD;
        return SW_OFFER_NET;
    }
    *note = net == ON_OFF ? NOTE_WIFI : net == NET_FAILED ? NOTE_RETRY : NOTE_NONE;
    return sd == SW_OK ? SW_OFFER_SD : sd == SW_BAD || sd == SW_FULL ? sd : SW_LATEST;
}

/* stock's own checks, check_updatefw_exist's answer as a page's: SW_CHECKING when they pass. */
int sw_stock_answer(int code) {
    return code == STOCK_FOUND    ? SW_CHECKING
           : code == STOCK_LOW    ? SW_LOW
           : code == STOCK_NOCARD ? SW_NOCARD
           : code == STOCK_NOFILE ? SW_NONE
           : code == STOCK_FULL   ? SW_FULL
                                  : SW_BAD;
}

/* ---- the words ---- */

/* The update's name from its version line: a release's or beta's packed digits (V1820 is 1.8.2,
 * V1822 1.8.2 beta 2; tools/build.py TAG), a test build's (V0JZi: dev_tag), releases before 1.8
 * (V101I), else the line itself. */
void sw_update_name(char *out, unsigned n, const char *tag) {
    char v[6] = { 0 };
    int digits = strlen(tag) == 5 && tag[0] == 'V';
    for (int i = 1; digits && i < 4; ++i) digits = tag[i] >= '0' && tag[i] <= '9';
    if (digits) v[0] = tag[1], v[1] = '.', v[2] = tag[2], v[3] = '.', v[4] = tag[3]; /* "1.8.2" */
    if (digits && tag[4] >= '1' && tag[4] <= '9')
        tk_snprintf(out, n, SW_PRODUCT " %s beta %c · %s", v, tag[4], tag);
    else if (digits && (tag[4] == '0' || tag[4] == 'I'))
        tk_snprintf(out, n, SW_PRODUCT " %s · %s", v, tag);
    else if (strlen(tag) == 5 && tag[0] == 'V' && tag[4] == 'i')
        tk_snprintf(out, n, SW_PRODUCT " Dev · %s", tag);
    else
        tk_snprintf(out, n, "%s", tag);
}

/* This build's version: "1.8.2 Dev", "1.8.2 beta 2", "1.8.2". */
static void sw_version(char *out, unsigned n) {
    char v[sizeof Q2POD_VERSION];
    const char *pod = strstr(Q2POD_VERSION, " Muse");
    tk_snprintf(v, sizeof v, "%s", Q2POD_VERSION);
    if (pod) v[pod - Q2POD_VERSION] = 0; /* "1.8.2", then " Dev" or the beta's " beta 2" */
    tk_snprintf(out, n, "%s%s", v, Q2POD_DEV ? " Dev" : pod ? pod + sizeof " Muse" - 1 : "");
}
/* This build as You're Up to Date says it: "1.8.2 Dev (V0JYi)", "1.8.2 beta 2 (V1822)", "1.8.2
 * (V1820)". */
void sw_installed(char *out, unsigned n) {
    char v[sizeof Q2POD_VERSION + 8];
    sw_version(v, sizeof v);
    tk_snprintf(out, n, "%s (" Q2POD_FW_TAG ") is installed", v);
}

/* ---- the pages ---- */

static void sw_close(void **page) {
    void *p = *page;
    *page = (void *)0;
    if (p) window_manager_close_window_force(window_manager(), p);
}

static int sw_tick(const void *unused);
static int sw_dl_tick(const void *unused);
static void sw_begin(void);
static int sw_key_up(void *ctx, void *event), sw_touch(void *ctx, void *event);

static int sw_gone(void *ctx, void *event) {
    if (ctx == swu.page) {
        swu.page = (void *)0;
        if (swu.state == SW_CHECKING)
            swu.cancel = 1; /* Return while it checks: the thread ends at its next read */
        if (swu.result == SW_DOWNLOADING)
            netupdate_cancel(); /* the card may be unmounted or shared over USB next */
    }
    return 0;
}

static void sw_result(int result) {
    swu.result = result;
    if (!swu.page) {
        if (!(swu.page = page_open("swupdate_page", sw_gone, sw_key_up))) return;
        widget_on(swu.page, EVT_POINTER_UP_BEFORE, sw_touch, 0);
        awake_screen();
    }
    widget_invalidate_force(swu.page, (void *)0);
}

/* The online check again, on its thread, the spinner over the page meanwhile. */
static void sw_net_start(void) {
    swu.online = ON_CHECKING;
    if (pthread_create(&swu.net_thread, 0, sw_net, 0)) {
        swu.online = NET_FAILED;
        return;
    }
    swu.net_up = 1;
    if (!swu.timer) swu.timer = timer_add(sw_tick, (void *)0, SW_SPIN_MS);
}

/* After Install and Restart: stock's dialog, which flashes and restarts; its answer when it can't.
 */
static int sw_install(const void *unused) {
    int quit = navigator_to("dialog/checkfw_dialog");
    if (quit == QUIT_NOFILE || quit == QUIT_LATEST || quit == QUIT_BAD)
        sw_result(quit == QUIT_NOFILE ? SW_NONE : quit == QUIT_LATEST ? SW_LATEST : SW_BAD);
    sw_remote_failed(quit);
    return 7; /* RET_REMOVE */
}
/* Install Update? (U2) on the confirmation card (docs/oled-design.md#confirmation-card) over the
 * check's page: the update's name as the title, what it is and what it takes as the body; Install
 * and Restart (row 1) runs stock's dialog from the next pass, either answer closes the page. */
static void sw_answer(void *ctx, int row, int toggle_on) {
    sw_close(&swu.page);
    if (row == 1) idle_add(sw_install, (void *)0); /* the dialog's own loop, after the card's */
}
static void sw_ask(void) {
    static const char *const rows[] = { "Cancel", "Install and Restart" };
    static char name[64], body[96];
    unsigned tenths = swu.size_kb * 128 / 12500; /* KB * 1024 / 100,000: tenths of a MB */
    swu.state = SW_ASKING;
    sw_update_name(name, sizeof name, swu.tag);
    tk_snprintf(body, sizeof body,
                "%u.%u MB, over %s. Takes about 2 min;\nyour music and settings stay.", tenths / 10,
                tenths % 10, Q2POD_FW_TAG);
    if (!confirm(&(confirm_t){ .title = "Install %s?",
                               .name = name,
                               .body = body,
                               .rows = rows,
                               .n_rows = 2,
                               .kind = CONFIRM_CONSEQUENTIAL,
                               .back = -1,
                               .answer = sw_answer }))
        sw_close(&swu.page);
}

/* The page's button: Download and Install, or Install. */
static void sw_go(void) {
    if (swu.result == SW_OFFER_SD) {
        sw_ask();
    } else if (swu.result == SW_OFFER_NET) {
        if (!netupdate_start()) {
            toast("Can't start the download");
            return;
        }
        swu.percent = swu.verifying = 0;
        sw_result(SW_DOWNLOADING);
        timer_add(sw_dl_tick, (void *)0, 250);
    }
}
/* The line under it. */
static void sw_note(void) {
    if (swu.note == NOTE_SD) {
        swu.note = NOTE_NONE, swu.focus = 0;
        sw_result(SW_OFFER_SD);
    } else if (swu.note == NOTE_WIFI) {
        sw_close(&swu.page);
        navigator_to("systemset/wifi_page");
    } else if (swu.note == NOTE_RETRY) {
        swu.note = NOTE_NONE, swu.focus = 0;
        sw_result(SW_CHECKING);
        sw_net_start();
        if (swu.online != ON_CHECKING) sw_result(sw_offer(swu.state, swu.online, &swu.note));
    }
}
static int sw_actions(void) {
    return (swu.result == SW_OFFER_NET || swu.result == SW_OFFER_SD) + (swu.note != NOTE_NONE);
}

/* navigation.c's keys on the page: the wheel moves the focus between the button and the line
 * under it (docs/oled-design.md#focus-ring); Centre acts on the one in focus, the button with no
 * ring, and leaves a result that has none; Return leaves, which ends a check or a download. */
int sw_update_key(void *top, unsigned key) {
    if (!swu.page || top != swu.page) return 0;
    int n = sw_actions(), button = swu.result == SW_OFFER_NET || swu.result == SW_OFFER_SD;
    if (key == KEY_NEXT || key == KEY_PREV) {
        int focus = button ? clamp_step(swu.focus, n - 1, key == KEY_NEXT ? 1 : -1) : 1;
        if (n && focus != swu.focus) swu.focus = focus;
        widget_invalidate_force(swu.page, (void *)0);
        return 1;
    }
    if (key != KEY_CENTER) return 0;
    if (swu.result == SW_CHECKING || swu.result == SW_DOWNLOADING) return 1;
    if (swu.note && (!button || (swu.focus == 1 && ui_focus_shown())))
        sw_note();
    else if (button)
        sw_go();
    else
        sw_close(&swu.page);
    return 1;
}
static int sw_key_up(void *ctx, void *event) {
    if ((unsigned)I(event, EVENT_KEY) != KEY_RETURN) return 0;
    sw_close(&swu.page);
    return STOP;
}
/* A tap on the button or on the line under it. */
static int sw_touch(void *ctx, void *event) {
    int x = I(event, EVENT_X), y = I(event, EVENT_Y);
    rect_t *b = &swu.button, *l = &swu.link;
    if (b->w && x >= b->x && x < b->x + b->w && y >= b->y && y < b->y + b->h)
        sw_go();
    else if (l->w && x >= l->x && x < l->x + l->w && y >= l->y && y < l->y + l->h)
        sw_note();
    return 0;
}

/* The checks' spinner, then their answer once neither thread runs: with the online part, the
 * offer or a result's page; the card's alone, Install Update? on the card over the check's page,
 * or a result's page. */
static int sw_tick(const void *unused) {
    /* and not while the check's page still slides in: a fast check (the emulator's took one
     * frame) shows the spinner before its answer replaces it */
    if (swu.state == SW_CHECKING || swu.online == ON_CHECKING ||
        (swu.page && window_manager_is_animating(window_manager()))) {
        swu.frame = (swu.frame + 1) % 12;
        if (swu.page) widget_invalidate_force(swu.page, (void *)0);
        return 8;
    }
    if (swu.sd_up) pthread_join(swu.thread, 0);
    if (swu.net_up) pthread_join(swu.net_thread, 0);
    swu.sd_up = swu.net_up = 0;
    swu.timer = 0;
    int state = swu.state;
    if (!swu.page || state == SW_CANCELLED) {
        sw_remote_end(SW_CANCELLED);
        return 7;
    }
    if (swu.online != ON_NONE) {
        swu.focus = 0;
        sw_result(sw_offer(state, swu.online, &swu.note));
    } else if (state == SW_OK) {
        if (!sw_remote_ok()) sw_ask();
    } else {
        sw_result(state);
        sw_remote_end(state);
    }
    return 7;
}

/* The download's progress on the page, then the card's check of what it fetched; a download that
 * ends without one says why and offers it again. */
static int sw_dl_tick(const void *unused) {
    int state = netupdate_poll(&swu.percent, &swu.verifying);
    if (state == NU_RUNNING) {
        if (swu.page) widget_invalidate_force(swu.page, (void *)0);
        return 8;
    }
    if (!swu.page) return 7;
    if (state == NU_DONE) {
        swu.online = ON_NONE;
        sw_begin();
    } else {
        toast(netupdate_said(state));
        sw_result(SW_OFFER_NET);
    }
    return 7;
}

/* The card's check (stock's first, then the tar's on a thread) under the page, beside the online
 * one when it runs. */
static void sw_begin(void) {
    swu.cancel = 0, swu.frame = 0;
    swu.level = I(g_power_capacity, 0);
    int answer = sw_stock_answer(check_updatefw_exist(SW_CARD, SW_FILE));
    swu.state = answer;
    sw_result(swu.online == ON_CHECKING ? SW_CHECKING : answer);
    if (!swu.page) {
        if (swu.net_up) pthread_join(swu.net_thread, 0);
        swu.net_up = 0;
        return;
    }
    if (answer == SW_CHECKING) {
        if (pthread_create(&swu.thread, 0, sw_check, 0)) {
            swu.state = SW_BAD;
            if (swu.online == ON_NONE) {
                sw_result(SW_BAD);
                return;
            }
        } else
            swu.sd_up = 1;
    }
    if (swu.state == SW_CHECKING || swu.online == ON_CHECKING)
        swu.timer = timer_add(sw_tick, (void *)0, SW_SPIN_MS);
    else if (swu.online != ON_NONE)
        sw_result(sw_offer(swu.state, swu.online, &swu.note));
}

/* fwupdate_tf: the card's update alone, after the online download (sw_dl_tick) or from stock's TF
 * row. */
int ringnav_fwupdate_tf(void) {
    if (swu.timer || swu.page || confirm_up()) return 0; /* one at a time */
    swu.online = ON_NONE, swu.note = swu.focus = 0;
    sw_begin();
    return 0;
}

/* General's Software Update: both checks at once, the online one while Wi-Fi is on. */
int sw_update_open(void *ctx, void *event) {
    if (swu.timer || swu.page || confirm_up()) return 0; /* one at a time */
    swu.note = swu.focus = 0;
    swu.button.w = swu.link.w = 0;
    swu.online = g_wififlag ? ON_CHECKING : ON_OFF;
    if (swu.online == ON_CHECKING) {
        if (pthread_create(&swu.net_thread, 0, sw_net, 0))
            swu.online = NET_FAILED;
        else
            swu.net_up = 1;
    }
    sw_begin();
    return 0;
}

/* playback.c ringnav_checkfw: stock's dialog, drawn as Restarting to Install; its label stays
 * stock's, unseen. */
static int sw_restart_gone(void *ctx, void *event) {
    if (ctx == swu.restart) swu.restart = (void *)0;
    return 0;
}
static int sw_restart_tick(const void *info) {
    if (!swu.restart) return 7;
    widget_invalidate_force(swu.restart, (void *)0);
    return 8;
}
void playback_going_down(int on); /* playback.c */
static int sw_dialog_gone(void *ctx, void *event) {
    playback_going_down(0); /* closed without the reboot: the queue's saves go on */
    return 0;
}
void sw_update_restarting(void *win) {
    if (win) widget_on(win, EVT_DESTROY, sw_dialog_gone, win);
    void *label = win ? widget_lookup(win, "scrlabel_checkfw", 1) : (void *)0;
    if (!label) return;
    widget_set_visible(label, 0, 0);
    swu.restart = win;
    widget_on(win, EVT_DESTROY, sw_restart_gone, win);
    widget_add_timer(win, sw_restart_tick, 250);
}

/* What stock's tar has unpacked, of SW_PARTS's whole, in px of w. */
static int sw_unpacked(int w) {
    static const char *const parts[] = { SW_PARTS "xImage", SW_PARTS "rootfs.squashfs" };
    long long got = 0;
    for (unsigned i = 0; i < 2; ++i) {
        void *f = fopen(parts[i], "rb");
        if (f && !fseek(f, 0, 2)) got += ftell(f);
        if (f) fclose(f);
    }
    unsigned whole = (unsigned)(swu.parts >> 10); /* in KB: 32-bit math */
    return !whole ? 0 : got >= swu.parts ? w : (int)((unsigned)(got >> 10) * (unsigned)w / whole);
}

/* "1.8.3" or the card's "Q2 Muse 1.8.3 · V1830" as the offer's name: the product and version,
 * without the tag. */
static void sw_offer_name(char *out, unsigned n) {
    if (swu.result == SW_OFFER_NET) {
        tk_snprintf(out, n, SW_PRODUCT " %s", swu.net_tag);
        return;
    }
    sw_update_name(out, n, swu.tag);
    char *dot = strstr(out, " · ");
    if (dot) *dot = 0;
}

/* The line under the offer's button or a result (accent text, the focus ring's underline when
 * the wheel is on it), its tap target kept. */
static void sw_note_paint(void *canvas, int y, int dy) {
    static const char *const said[] = { "", "Install from SD Card instead",
                                        "Turn on Wi-Fi to check online",
                                        "Couldn't check online · Try Again" };
    int w;
    swu.link.w = 0;
    if (!swu.note) return;
    w = ui_measure(canvas, said[swu.note], "inter", TYPE(15));
    ui_line(canvas, said[swu.note], 0, 375, y, 1, "inter", TYPE(15), tok(TOK_ACCENT_TEXT));
    if (swu.focus == 1 && ui_focus_shown()) {
        rect_t u = { (375 - w) / 2, y + 10, w, 2 };
        fill_box(canvas, &u, RGBA(tok(TOK_ACCENT_TEXT)), 1);
    }
    swu.link = (rect_t){ (375 - w) / 2 - 8, y - SW_LINK_H / 2 + dy, w + 16, SW_LINK_H };
}

/* The offer (29 v3): the source's glyph on the accent's circle, the update's name, where it is
 * and its size, what is installed, the button and the line under it. */
static void sw_offer_paint(void *w, void *canvas, int dy, unsigned accent) {
    int top = -dy, net = swu.result == SW_OFFER_NET, cx = 375 / 2;
    unsigned kb = net ? swu.net_kb : swu.size_kb, tenths = kb * 128 / 12500;
    char s[64];
    rect_t circle = { cx - SW_OFFER_R, top + SW_OFFER_CY - SW_OFFER_R, 2 * SW_OFFER_R,
                      2 * SW_OFFER_R };
    fill_box(canvas, &circle, RGBA(accent), SW_OFFER_R);
    paint_art(w, canvas, theme_art(net ? "swoffer_net" : "swoffer_sd"), cx - SW_OFFER_GLYPH / 2,
              top + SW_OFFER_CY - SW_OFFER_GLYPH / 2);
    sw_offer_name(s, sizeof s);
    ui_line(canvas, s, 0, 375, top + SW_OFFER_NAME_Y, 1, "inter-medium", TYPE(22),
            tok(TOK_PRIMARY));
    if (tenths)
        tk_snprintf(s, sizeof s, "From the %s · %u.%u MB", net ? "internet" : "SD card",
                    tenths / 10, tenths % 10);
    else
        tk_snprintf(s, sizeof s, "From the %s", net ? "internet" : "SD card");
    ui_line(canvas, s, 0, 375, top + SW_OFFER_FROM_Y, 1, "inter", TYPE(15), tok(TOK_SECONDARY));
    char v[sizeof Q2POD_VERSION + 8];
    sw_version(v, sizeof v);
    tk_snprintf(s, sizeof s, "Installed: %s", v);
    ui_line(canvas, s, 0, 375, top + SW_OFFER_HAVE_Y, 1, "inter", TYPE(13), tok(TOK_TERTIARY));
    const char *label = net ? "Download and Install" : "Install";
    int bw = ui_button(canvas, cx, top + SW_OFFER_BTN_Y, label, 1, swu.focus == 0, 0);
    swu.button = (rect_t){ cx - bw / 2, top + SW_OFFER_BTN_Y - CTA_H / 2 + dy, bw, CTA_H };
    sw_note_paint(canvas, top + SW_OFFER_LINE_Y, dy);
}

/* ringnav_paint, after w's children. */
void sw_update_paint(void *w, void *canvas) {
    void *lcd = w ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || (w != swu.page && w != swu.restart)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    int light = ipod_light(), dy = I(w, W_Y);
    unsigned accent = scheme()->tones[0] & 0xffffffu;
    char s[64], spin[24];
    st.raw_ink = 1;
    if (w == swu.page && (swu.result == SW_OFFER_NET || swu.result == SW_OFFER_SD)) {
        sw_offer_paint(w, canvas, dy, accent);
    } else if (w == swu.page) {
        mode_page_t m = { RGB_NEGATIVE,
                          "modepage_bad",
                          0,
                          "Update Can't Be Used",
                          "The file is damaged or not for the Q2",
                          (const char *)0 };
        switch (swu.result) {
        case SW_CHECKING:
            tk_snprintf(spin, sizeof spin, "swspin_%c_%d", light ? 'l' : 'd', swu.frame);
            m = (mode_page_t){ MODE_BARE,
                               spin,
                               SW_SPIN_PX,
                               "Checking for an Update…",
                               swu.online == ON_CHECKING ? "Looking online and on the SD card"
                                                         : "Looking on the SD card",
                               (const char *)0 };
            break;
        case SW_LATEST:
            sw_installed(s, sizeof s);
            m = (mode_page_t){ RGB_POSITIVE,   "modepage_done", 0, "You're Up to Date", s,
                               (const char *)0 };
            break;
        case SW_NONE:
            m = (mode_page_t){ RGB_OFF,
                               "modepage_none",
                               0,
                               "No Update Found",
                               "Put update.tar on the SD card",
                               (const char *)0 };
            break;
        case SW_NOCARD:
            m = (mode_page_t){
                RGB_OFF,        "modepage_none", 0, "No SD Card", "Insert the card with update.tar",
                (const char *)0
            };
            break;
        case SW_LOW:
            tk_snprintf(s, sizeof s, "Needs %d%% or a charger · now %d%%", SW_BATTERY, swu.level);
            m = (mode_page_t){ RGB_WARNING, "modepage_low", 0, "Charge to Update",
                               s,           (const char *)0 };
            break;
        case SW_FULL:
            m = (mode_page_t){
                RGB_WARNING,    "modepage_bad", 0, "Not Enough Space", "Free 100 MB on the SD card",
                (const char *)0
            };
            break;
        case SW_DOWNLOADING:
            tk_snprintf(s, sizeof s, "%d%%", swu.percent);
            m = (mode_page_t){ accent,
                               "modepage_check",
                               0,
                               "Downloading the Update",
                               swu.verifying ? "Verifying…"
                               : swu.percent ? s
                                             : "Starting…",
                               (const char *)0 };
        }
        int top = mode_page_paint(w, canvas, dy, &m);
        swu.button.w = 0;
        if (swu.result == SW_DOWNLOADING) { /* the restart's bar, filling as it comes */
            rect_t track = { 375 / 2 - SW_BAR_W / 2, top + SW_BAR_Y, SW_BAR_W, SW_BAR_H },
                   part = track;
            part.w = SW_BAR_W * swu.percent / 100;
            fill_box(canvas, &track, RGBA(tok(TOK_TRACK)), SW_BAR_H / 2);
            if (part.w >= SW_BAR_H) fill_box(canvas, &part, RGBA(accent), SW_BAR_H / 2);
        }
        if (swu.result != SW_CHECKING && swu.result != SW_DOWNLOADING)
            sw_note_paint(canvas, top + SW_NOTE_Y, dy);
        else
            swu.link.w = 0;
    } else {
        /* stock's dialog covers the status row: its title drawn in its place and face */
        ui_text(canvas, "Software Update", 1, SW_STATUS_PX, tok(TOK_SECONDARY), 1, STATUS_ROW_INSET,
                geom_val(V_STATUS_Y) - dy, 240, 2);
        int top =
            mode_page_paint(w, canvas, dy,
                            &(mode_page_t){ accent, "modepage_restart", 0, "Restarting to Install",
                                            "Don't turn off the Q2", (const char *)0 });
        rect_t track = { 375 / 2 - SW_BAR_W / 2, top + SW_BAR_Y, SW_BAR_W, SW_BAR_H }, part = track;
        part.w = sw_unpacked(SW_BAR_W);
        fill_box(canvas, &track, RGBA(tok(TOK_TRACK)), SW_BAR_H / 2);
        if (part.w >= SW_BAR_H) fill_box(canvas, &part, RGBA(accent), SW_BAR_H / 2);
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* The status row's title on the result pages; 0 for another page (navigation.c's bar titles). */
const char *sw_update_title(const char *name) {
    return !tk_strcmp(name, "swupdate_page") ? "Software Update" : (const char *)0;
}

#if Q2POD_DEV
/* q2update (patch/q2update.sh, docs/adb.md#installing-an-update-over-adb): a computer asks, over
 * adb, for the card's update.tar to be checked (check, the dry run) or installed (install), or for
 * this build's tag (version). The helper writes "NONCE OP" to Q2UPDATE_REQUEST through a temporary
 * name, root's alone (mode 0600), as q2guard writes its state file; q2update_poll (headset.c, every
 * 500 ms with q2eq_poll) takes it on the UI thread and runs this file's own path,
 * ringnav_fwupdate_tf's: the same page, stock's checks, the tar's check and stock's checkfw_dialog,
 * only with no Install Update? card, as no one is at the screen. Each step is answered in
 * Q2UPDATE_ACK, written whole (.tmp, then renamed): "NONCE WORD INSTALLED UPDATE", WORD taken (the
 * check started), then one of version, ok (check passed, nothing installed), installing (stock's
 * dialog runs next), busy, unknown, low, nocard, nofile, full, bad, latest, cancelled, or failed
 * (stock's dialog refused after all); INSTALLED is this build's tag, UPDATE the file's or -. */
#ifndef Q2UPDATE_ROOT
#define Q2UPDATE_ROOT ""
#endif
#define Q2UPDATE_REQUEST Q2UPDATE_ROOT "/tmp/q2update.req"
#define Q2UPDATE_ACK Q2UPDATE_ROOT "/tmp/q2update.ack"
enum { SWR_NONE, SWR_CHECK, SWR_INSTALL };
static struct {
    unsigned nonce, installing; /* the request the page is running; the one stock's dialog runs */
    int mode;
} swr __attribute__((section(".scratch")));

static void sw_remote_ack(unsigned nonce, const char *word) {
    char line[64];
    int n = tk_snprintf(line, sizeof line, "%u %s " Q2POD_FW_TAG " %s\n", nonce, word,
                        swu.tag[0] && tk_strcmp(word, "version") ? swu.tag : "-");
    void *f = fopen(Q2UPDATE_ACK ".tmp", "wb");
    if (!f) return;
    fwrite(line, 1, n, f);
    fclose(f);
    rename(Q2UPDATE_ACK ".tmp", Q2UPDATE_ACK);
}
/* The page's answer for the request it runs, as its word. */
static void sw_remote_end(int state) {
    static const char *const words[] = { "taken", "ok",  "latest", "nofile",   "nocard",
                                         "low",   "bad", "full",   "cancelled" };
    if (!swr.nonce) return;
    sw_remote_ack(swr.nonce, state >= SW_CHECKING && state <= SW_CANCELLED ? words[state] : "bad");
    swr.nonce = 0, swr.mode = SWR_NONE;
}
/* The tar is good: install goes on as Install and Restart does, check stops here; 0 with no
 * request, so the card asks as ever. */
static int sw_remote_ok(void) {
    unsigned nonce = swr.nonce;
    int mode = swr.mode;
    if (!nonce) return 0;
    swr.nonce = 0, swr.mode = SWR_NONE;
    if (mode == SWR_INSTALL) {
        swr.installing = nonce;
        sw_remote_ack(nonce, "installing");
        sw_answer((void *)0, 1, 0); /* the page closes, stock's dialog from the next pass */
    } else {
        sw_remote_ack(nonce, "ok");
        sw_close(&swu.page);
        toast("Update checked: ready to install");
    }
    return 1;
}
static void sw_remote_failed(int quit) {
    if (swr.installing) sw_remote_ack(swr.installing, "failed");
    swr.installing = 0;
}

void q2update_poll(void) {
    char text[32];
    int st[40]; /* glibc 2.28 MIPS o32 stat: st_mode word 5, st_uid word 7 */
    unsigned nonce = 0, i = 0;
    if (__xstat(3, Q2UPDATE_REQUEST, st) || (st[5] & 0170077) != 0100000 || st[7]) return;
    void *f = fopen(Q2UPDATE_REQUEST, "rb");
    unsigned n = f ? fread(text, 1, sizeof text - 1, f) : 0;
    if (f) fclose(f);
    unlink(Q2UPDATE_REQUEST);
    text[n] = 0;
    for (; text[i] >= '0' && text[i] <= '9'; ++i) nonce = nonce * 10 + (unsigned)(text[i] - '0');
    if (!i) return; /* no nonce: no one to answer */
    const char *op = text + i + (text[i] == ' ');
    int len = 0;
    while (op[len] && op[len] != '\n' && op[len] != ' ') ++len;
    int mode = len == 5 && !strncmp(op, "check", 5)     ? SWR_CHECK
               : len == 7 && !strncmp(op, "install", 7) ? SWR_INSTALL
                                                        : SWR_NONE;
    swu.tag[0] = 0;
    if (len == 7 && !strncmp(op, "version", 7)) {
        sw_remote_ack(nonce, "version");
        return;
    }
    if (mode == SWR_NONE || text[i] != ' ') {
        sw_remote_ack(nonce, "unknown");
        return;
    }
    if (swr.nonce || swu.timer || swu.page || swu.restart || confirm_up()) {
        sw_remote_ack(nonce, "busy");
        return;
    }
    swr.nonce = nonce, swr.mode = mode;
    sw_remote_ack(nonce, "taken");
    ringnav_fwupdate_tf();
    /* card-only, so no timer means the answer is in: stock's checks refused, or no thread */
    if (!swu.timer) sw_remote_end(swu.page ? swu.state : SW_CANCELLED);
}
#endif
