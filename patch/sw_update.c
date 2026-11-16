/* SD Card Update (docs/internals.md#sd-card-update, docs/oled-design.md#software-update): System
 * Update's first row, and Network Update once its download is on the card, run fwupdate_tf
 * (0x4c7eb4), hooked whole here. Stock asked first, then checked, then flashed; here the card's
 * update.tar is checked first, on a thread, while a page says so, and only an update stock's
 * updater would take is offered:
 *
 * - stock's own checks first (check_updatefw_exist, on the UI thread as stock's): the battery
 * (under 16% with no cable in: Charge to Update), the card, the file, and room for its unpacking
 * (No SD Card, No Update Found, Not Enough Space), each a mode page at once;
 * - then Checking for an Update... (the page's spinner) while sw_check reads the tar as
 * update_firmware (0x5028e0) will: firmware_v20.info first, "Shanling Q2" and a version holding a V
 * (else Update Can't Be Used), the version not this build's (else You're Up to Date: stock refuses
 * an equal one), and the MD5s its third and fourth lines give for recovery-update/xImage and
 * rootfs.squashfs, read from the tar as they stream by (else Can't Be Used);
 * - then the house dialog, Install Update?: the update's version, this build's tag and the file's
 * size, Cancel (selected) and Install and Restart, which runs stock's checkfw_dialog as stock's
 * fwupdate_tf did: its thread unpacks and checks the tar again, marks the next boot as the
 * recovery's and restarts, and the recovery flashes. That dialog is drawn as a mode page here
 * (Restarting to Install), with the unpacking's progress; should its own check fail after all, the
 * dialog's answer opens the page it means. */
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
    SW_ASKING /* swu.state: OK taken, Install Update? opened */
};
/* stock's answers: check_updatefw_exist's, and checkfw_dialog's dialog_quit codes (fwupdate_tf's
 * own tests) */
enum { STOCK_FOUND = 1, STOCK_NOCARD = -2, STOCK_NOFILE = -11, STOCK_FULL = -12, STOCK_LOW = -15 };
enum { QUIT_NOFILE = 9, QUIT_LATEST = 13, QUIT_BAD = 18 };

/* The install page (U2): its head lines above the rows, the footer under them (Text Tertiary). */
#define SW_HEAD 54    /* the rows' top below the list's, for the two lines: y 112 on screen */
#define SW_NAME_DY 46 /* the update's name, Inter Medium 17, centred this far above the rows */
#define SW_SIZE_DY 24 /* "Installed: ...", Inter Regular 14 */
#define SW_FOOT_DY                                                                                 \
    20 /* the footer's first line under the rows, Inter Regular 13, the next 18 lower */
#define SW_FOOT_LINE 18
#define SW_NAME_PX 20
#define SW_SIZE_PX 17
#define SW_FOOT_PX 16
/* The restart's bar (U3): under the stack's line, 180 x 6 with round ends */
#define SW_BAR_Y 172
#define SW_BAR_W 180
#define SW_BAR_H 6
#define SW_STATUS_PX 18 /* the status row's title, Inter Medium 15 */
#define SW_ORANGE 0xff9f0a
#define SW_RED 0xff453a
#define SW_GREY 0x3a3a3c

static struct {
    unsigned long thread;
    volatile int state, cancel; /* state: SW_CHECKING while sw_check runs, then its answer */
    int result, level, ready, frame;
    unsigned timer, size_kb;
    long long parts; /* xImage's and rootfs.squashfs's bytes, what stock's tar unpacks */
    void *page, *confirm, *view, *restart;
    char tag[24]; /* the update's version line */
} swu __attribute__((section(".scratch")));

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
        tk_snprintf(out, n, "Q2 Pod %s beta %c · %s", v, tag[4], tag);
    else if (digits && (tag[4] == '0' || tag[4] == 'I'))
        tk_snprintf(out, n, "Q2 Pod %s · %s", v, tag);
    else if (strlen(tag) == 5 && tag[0] == 'V' && tag[4] == 'i')
        tk_snprintf(out, n, "Q2 Pod Dev · %s", tag);
    else
        tk_snprintf(out, n, "%s", tag);
}

/* This build as You're Up to Date says it: "1.8.2 Dev (V0JYi)", "1.8.2 beta 2 (V1822)", "1.8.2
 * (V1820)". */
void sw_installed(char *out, unsigned n) {
    char v[sizeof Q2POD_VERSION];
    const char *pod = strstr(Q2POD_VERSION, " Pod");
    tk_snprintf(v, sizeof v, "%s", Q2POD_VERSION);
    if (pod) v[pod - Q2POD_VERSION] = 0; /* "1.8.2", then " Dev" or the beta's " beta 2" */
    tk_snprintf(out, n, "%s%s (" Q2POD_FW_TAG ") is installed", v,
                Q2POD_DEV ? " Dev"
                : pod     ? pod + 4
                          : "");
}

/* ---- the pages ---- */

static void sw_close(void **page) {
    void *p = *page;
    *page = (void *)0;
    if (p) window_manager_close_window_force(window_manager(), p);
}

static int sw_gone(void *ctx, void *event) {
    if (ctx == swu.page) {
        swu.page = (void *)0;
        if (swu.state == SW_CHECKING)
            swu.cancel = 1; /* Return while it checks: the thread ends at its next read */
    } else if (ctx == swu.confirm)
        swu.confirm = swu.view = (void *)0;
    return 0;
}
/* Return or Centre leaves a result; the check is left with Return. */
static int sw_key(void *ctx, void *event) {
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key != KEY_RETURN && (key != KEY_CENTER || swu.result == SW_CHECKING)) return 0;
    sw_close(&swu.page);
    return STOP;
}

static void sw_result(int result) {
    swu.result = result;
    if (!swu.page) {
        if (!(swu.page = page_open("swupdate_page", sw_gone, sw_key))) return;
        awake_screen();
    }
    widget_invalidate_force(swu.page, (void *)0);
}

/* After Install and Restart: stock's dialog, which flashes and restarts; its answer when it can't.
 */
static int sw_install(const void *unused) {
    int quit = navigator_to("dialog/checkfw_dialog");
    if (quit == QUIT_NOFILE || quit == QUIT_LATEST || quit == QUIT_BAD)
        sw_result(quit == QUIT_NOFILE ? SW_NONE : quit == QUIT_LATEST ? SW_LATEST : SW_BAD);
    return 7; /* RET_REMOVE */
}
static int sw_click(void *ctx, void *event) {
    sw_close(&swu.confirm);
    if (ctx) idle_add(sw_install, (void *)0); /* the dialog's own loop, after this click's */
    return 0;
}
static void sw_confirm_open(void) {
    if (!(swu.confirm = page_open("swconfirm_page", sw_gone, page_keyup))) return;
    swu.ready = 0;
    void *view = swu.view = page_list(swu.confirm, swu.confirm, 0, "Install Update?", 2, 32);
    void *list = P(view, W_PARENT);
    widget_move_resize(list, I(list, W_X), I(list, W_Y) + SW_HEAD, I(list, W_W), I(list, W_H));
    page_row_detail(view, 0, "Cancel", 0, sw_click);
    page_row_detail(view, 1, "Install and Restart", 0,
                    sw_click); /* its index, 1, is the click's ctx */
    ringnav_select(view, 0, 2);
}

/* Whether page is open and still: the window manager has marked it opened, after its slide in
 * (navigation.c empty_toast's test), and nothing slides. */
static int sw_opened(void *page) {
    return widget_get_prop_int(page, "stage", 0) == WINDOW_STAGE_OPENED &&
           !window_manager_is_animating(window_manager());
}

/* Install Update? once it is on top and opened (it opens from a timer, as usb_ui.c usb_ready's
 * pages): the wheel's list on it, Cancel selected, and the check's page under it closed only then,
 * not while its slide in runs. Whether it is. */
static int sw_settle(void) {
    if (swu.ready || !swu.confirm) return 1;
    if (!sw_opened(swu.confirm) || window_manager_get_top_window(window_manager()) != swu.confirm)
        return 0;
    swu.ready = 1;
    sw_close(&swu.page);
    ringnav_select(swu.view, 0, 2);
    widget_invalidate_force(swu.confirm, (void *)0);
    return 1;
}

/* The check's spinner, its answer once the thread has one, then Install Update? settling. */
static int sw_tick(const void *unused) {
    if (swu.state == SW_ASKING) {
        if (swu.confirm && !sw_settle()) return 8; /* RET_REPEAT */
        sw_close(&swu.page); /* Install Update? left before it settled: the check's page too */
        swu.timer = 0;
        return 7; /* RET_REMOVE */
    }
    /* and not before the check's page has opened: a page opened while it slides in is held back
     * by the window manager until that ends, and took no input once it showed */
    if (swu.state == SW_CHECKING || (swu.page && !sw_opened(swu.page))) {
        swu.frame = (swu.frame + 1) % 12;
        if (swu.page) widget_invalidate_force(swu.page, (void *)0);
        return 8;
    }
    pthread_join(swu.thread, 0);
    int state = swu.state;
    if (state == SW_OK && swu.page) {
        swu.state = SW_ASKING;
        sw_confirm_open();
        if (!swu.confirm) sw_close(&swu.page);
        if (swu.confirm && !sw_settle()) return 8;
    } else if (state != SW_CANCELLED && swu.page)
        sw_result(state);
    swu.timer = 0;
    return 7;
}

/* fwupdate_tf: System Update's SD Card Update row, and Network Update's download done. */
int ringnav_fwupdate_tf(void) {
    if (swu.timer || swu.page || swu.confirm) return 0; /* one at a time */
    swu.cancel = 0, swu.frame = 0;
    swu.level = I(g_power_capacity, 0);
    int answer = sw_stock_answer(check_updatefw_exist(SW_CARD, SW_FILE));
    sw_result(answer);
    if (answer != SW_CHECKING || !swu.page) return 0;
    swu.state = SW_CHECKING;
    if (pthread_create(&swu.thread, 0, sw_check, 0)) {
        sw_result(SW_BAD);
        return 0;
    }
    swu.timer = timer_add(sw_tick, (void *)0, SW_SPIN_MS);
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
void sw_update_restarting(void *win) {
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

/* ringnav_paint, after w's children. */
void sw_update_paint(void *w, void *canvas) {
    void *lcd = w ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || (w != swu.page && w != swu.confirm && w != swu.restart)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    int light = ipod_light(), dy = I(w, W_Y), x = geom_val(V_TEXT_X), tw = 375 - 2 * x;
    unsigned accent = scheme()->tones[0] & 0xffffffu, ink = light ? 0x000000 : 0xffffff,
             tertiary = light ? 0x6e6e6e : 0x858585;
    char s[64], spin[24];
    st.raw_ink = 1;
    if (w == swu.page) {
        mode_page_t m = { SW_RED,
                          "swmode_bad",
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
                               "Looking on the SD card",
                               (const char *)0 };
            break;
        case SW_LATEST:
            sw_installed(s, sizeof s);
            m = (mode_page_t){ MODE_GREEN, "swmode_latest", 0, "You're Up to Date",
                               s,          (const char *)0 };
            break;
        case SW_NONE:
            m = (mode_page_t){ SW_GREY,
                               "swmode_none",
                               0,
                               "No Update Found",
                               "Put update.tar on the SD card",
                               (const char *)0 };
            break;
        case SW_NOCARD:
            m = (mode_page_t){
                SW_GREY,        "swmode_none", 0, "No SD Card", "Insert the card with update.tar",
                (const char *)0
            };
            break;
        case SW_LOW:
            tk_snprintf(s, sizeof s, "Needs %d%% or a charger · now %d%%", SW_BATTERY, swu.level);
            m = (mode_page_t){ SW_ORANGE, "swmode_low", 0, "Charge to Update", s, (const char *)0 };
            break;
        case SW_FULL:
            m = (mode_page_t){
                SW_ORANGE,      "swmode_bad", 0, "Not Enough Space", "Free 100 MB on the SD card",
                (const char *)0
            };
        }
        mode_page_paint(w, canvas, dy, &m);
    } else if (w == swu.confirm && swu.view) {
        void *list = P(swu.view, W_PARENT);
        sw_settle();
        int rows = I(list, W_Y), foot = rows + I(list, W_H) + SW_FOOT_DY;
        sw_update_name(s, sizeof s, swu.tag);
        ar_text(canvas, s, 1, SW_NAME_PX, ink, 1, x, rows - SW_NAME_DY, tw, 2);
        unsigned tenths = swu.size_kb * 128 / 12500; /* KB * 1024 / 100,000: tenths of a MB */
        tk_snprintf(s, sizeof s, "Installed: %s · %u.%u MB on the SD card", Q2POD_FW_TAG,
                    tenths / 10, tenths % 10);
        ar_text(canvas, s, 0, SW_SIZE_PX, tertiary, 1, x, rows - SW_SIZE_DY, tw, 2);
        ar_text(canvas, "Takes about 2 minutes. Keep the Q2 charged;", 0, SW_FOOT_PX, tertiary, 1,
                x, foot, tw, 2);
        ar_text(canvas, "your music and settings stay.", 0, SW_FOOT_PX, tertiary, 1, x,
                foot + SW_FOOT_LINE, tw, 2);
    } else {
        /* stock's dialog covers the status row: its title drawn in its place and face */
        ar_text(canvas, "Software Update", 1, SW_STATUS_PX, light ? 0x5c5c5c : 0xababab, 1,
                STATUS_ROW_INSET, geom_val(V_STATUS_Y) - dy, 240, 2);
        int top =
            mode_page_paint(w, canvas, dy,
                            &(mode_page_t){ accent, "swmode_restart", 0, "Restarting to Install",
                                            "Don't turn off the Q2", (const char *)0 });
        rect_t track = { 375 / 2 - SW_BAR_W / 2, top + SW_BAR_Y, SW_BAR_W, SW_BAR_H }, part = track;
        part.w = sw_unpacked(SW_BAR_W);
        fill_box(canvas, &track, RGBA(light ? 0xd1d1d6 : 0x333333), SW_BAR_H / 2);
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
