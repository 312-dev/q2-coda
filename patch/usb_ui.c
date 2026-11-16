/* The USB pages (docs/internals.md#usb-pages, docs/oled-design.md#usb): what a cable into a computer
 * shows. Stock starts a gadget the moment a cable is in (check_usb_status, hooked as
 * ringnav_usb_status in usb_charge.c, which calls usb_ui_status around it) and, once a computer
 * configures it, opens usbmode_page in the mode Quick Settings' USB tile last chose. Here:
 *
 * - A cable in with no remembered choice keeps stock out (g_usblink_status 3, which its next pass
 *   leaves alone until the cable comes out) and starts stock's storage gadget with no card in it
 *   (a reader with no medium), only to learn whether a computer answers. One that configures it
 *   within USB_PROBE_MS gets the chooser, "Use USB For": Charge Only, Transfer Files, USB Audio and,
 *   in a --dev build, USB Debugging, and Always Do This. Nothing answering is a charger: the probe
 *   stops and usb_charger_in (charging.c) toasts the charge. A cable in at all tells charging.c
 *   (charge_plugged), whose plug-in screen lights a dark Q2.
 * - Transfer Files and USB Audio hand back to stock: the probe stops, g_usbmode is set for this
 *   cable (the tile's choice again once it is out, unless Always Do This saved it) and
 *   g_usblink_status 0 lets stock's next pass start its gadget and open usbmode_page, which is
 *   drawn here as a mode page: one solid circle and its glyph, a title, a line and a Stop pill
 *   (Centre, as Return, leaves the mode). Charge Only stops the probe. USB Debugging stops it too,
 *   which in a --dev build hands the controller back to the ADB gadget (docs/adb.md), and opens
 *   its own mode page, whose Stop parks the ADB gadget.
 * - A remembered choice skips the chooser: Transfer Files and USB Audio follow the tile, so the
 *   tile still changes them; Charge Only and USB Debugging hold until the tile is changed.
 * - Toasts: Unplugged, OK to Disconnect (the computer's eject while USB mode is not on top) and
 *   USB Audio Ready (the DAC mode reached without the chooser).
 * - --dev builds: adbd's question about an unknown computer (docs/adb.md#authorization), "Allow USB
 *   Debugging?", with the key's fingerprint, Cancel, Allow and Always Allow This Computer. */
#include "navigation.h"

#define USB_PROBE_MS 3000 /* a computer configures a gadget within a second or two */
#define USB_UDC "/sys/class/udc/13500000.otg/state"
#define USB_PICK_KEY "USBPICK" /* [Q2POD]: the remembered choice, USB_* */
#define USB_PICK_MODE "USBPICKMODE" /* ... and the tile's mode when it was saved */
enum { USB_ASK, USB_CHARGE, USB_FILES, USB_AUDIO, USB_DEBUG, USB_CHOICES };
#define USB_LINK_HELD 3 /* g_usblink_status: stock leaves the cable to us until it is out */
#define USB_MODE_DAC 1  /* g_usbmode: Audio and DAC; anything else USB storage (2, stock's) */
#define USB_MODE_FILES 2

/* The chooser (U1): rows from USB_ROW_TOP, the selected one USB_ROW_TALL with its caption, the
 * others USB_ROW; the switch row USB_GAP below them. Glyph centred on USB_ICON_X, words at
 * USB_TEXT_X (tools/art.py USB_ROW_PX). */
#define USB_ROW 34
#define USB_ROW_TALL 50
#define USB_GAP 6
#define USB_ICON_X 38
#define USB_ICON 19
#define USB_TEXT_X 58
#define USB_CAP_Y 37 /* the caption's centre in its row */
#define USB_CAP_PX 17 /* Inter Regular 14 */
#define USB_CAP_RGB 0xdce6fa

/* A mode page (U2-U6): the stack, circle, title, line and the optional Stop pill, centred as one
 * between the status row's foot (MODE_TOP) and the screen's bottom. */
#define MODE_TOP 40
#define MODE_STACK 158
#define MODE_STACK_PILL 204
#define MODE_R 46
#define MODE_GLYPH 44 /* tools/art.py USB_MODE_PX */
#define MODE_TITLE_Y 120
#define MODE_TITLE_PX 26 /* Inter Medium 22 */
#define MODE_LINE_Y 148
#define MODE_LINE_PX 18 /* Inter Regular 15 */
#define MODE_PILL_Y 172
#define MODE_PILL_H 32
#define MODE_PILL_PAD 22
#define MODE_PILL_PX 20 /* Inter Medium 17 */
#define MODE_PILL_RGB 0x2c2c2e
#define MODE_GREEN 0x34c759
#define MODE_TEXT_LINE 32

/* The ADB prompt (U5) under its title: the key's lines, then Cancel, Allow, the switch and the note. */
#define AUTH_SOCKET "/tmp/q2adb-ui" /* tools/adbd.patch adb_auth_client.c ADB_UI_SOCKET */
#define AUTH_KEYS "/mnt/data/adb_keys"
#define AUTH_KEY_MAX 1024
#define AUTH_HEAD 52 /* "This computer's key:" and the fingerprint above the rows */
#define AUTH_NOTE_PX 17 /* Inter Regular 14, Text Tertiary */

static struct {
    int view_ready, auth_ready;
    const char *toast_glyph, *toast_text;
    int det, probing, choice, mode_saved /* g_usbmode + 1 for this cable's, 0 none */, ejected, ejected_told, chosen, row_sel;
    unsigned probe_at;
    void *chooser, *view, *always, *debug, *mode;
    /* the ADB prompt */
    int fd, got, auth_always;
    unsigned auth_at;
    void *auth, *auth_view, *auth_switch;
    char in[AUTH_KEY_MAX + 8], key[AUTH_KEY_MAX], fp[32];
} u __attribute__((section(".scratch")));

static int usb_host(void) {
    char s[16] = { 0 };
    void *f = fopen(USB_UDC, "rb");
    if (!f) return 0;
    fread(s, 1, sizeof s - 1, f);
    fclose(f);
    return !memcmp(s, "configured", 10);
}

/* A toast, shown by the next ringnav_sleep poll (usb_ui_poll): never inside stock's USB pass, whose
 * own loop a toast's would run again. */
static void usb_toast(const char *glyph, const char *text) {
    if (!g_backlight_status) return;
    u.toast_glyph = glyph, u.toast_text = text;
}

/* The choice this cable gets without asking, USB_ASK for none. Transfer Files and USB Audio follow
 * the tile; Charge Only and USB Debugging are dropped once the tile has changed since they were
 * saved. */
static int usb_remembered(void) {
    int pick = config_value("Q2POD", USB_PICK_KEY, 0, USB_ASK);
    if (pick <= USB_ASK || pick >= USB_CHOICES || (pick == USB_DEBUG && !Q2POD_DEV)) return USB_ASK;
    if (pick == USB_FILES || pick == USB_AUDIO) return g_usbmode == USB_MODE_DAC ? USB_AUDIO : USB_FILES;
    if (config_value("Q2POD", USB_PICK_MODE, 0, -1) != g_usbmode) return USB_ASK;
    return pick;
}

/* g_usbmode for this cable; the tile's comes back when it is out. */
static void usb_mode(int audio) {
    if (!u.mode_saved) u.mode_saved = g_usbmode + 1; /* 0: none saved */
    g_usbmode = audio ? USB_MODE_DAC : USB_MODE_FILES;
}

static void usb_page_close(void **page) {
    void *p = *page;
    *page = (void *)0;
    if (p) window_manager_close_window_force(window_manager(), p);
}

static int usb_debug_open(void);

/* A choice made (from the chooser or remembered); always saves it. */
static void usb_apply(int pick, int always) {
    u.choice = pick;
    if (u.probing || u.chosen) system("storage_config.sh stop >/dev/null 2>&1");
    u.probing = 0;
    if (always) {
        write_int_config(pick, "Q2POD", USB_PICK_KEY);
        if (pick == USB_FILES || pick == USB_AUDIO) {
            g_usbmode = pick == USB_AUDIO ? USB_MODE_DAC : USB_MODE_FILES;
            write_int_config(g_usbmode, "SYSSET", "USBMODE"); /* the tile's own */
            u.mode_saved = 0;
        }
        write_int_config(g_usbmode, "Q2POD", USB_PICK_MODE);
    }
    if (pick == USB_FILES || pick == USB_AUDIO) {
        if (!always) usb_mode(pick == USB_AUDIO);
        g_usblink_status = 0; /* stock's next pass starts its gadget and opens usbmode_page */
    } else if (pick == USB_DEBUG) {
        usb_debug_open();
    }
}

/* ---- the chooser ---- */

static const char *const usb_names[] = { "Charge Only", "Transfer Files", "USB Audio", "USB Debugging" };
static const char *const usb_caps[] = { "Keep listening while it charges", "Your computer opens the SD card",
                                        "Play your computer's sound here", "Developer builds only" };
static const char *const usb_glyphs[] = { "usb_charge", "usb_files", "usb_audio", "usb_debug" };
#define USB_ROWS (Q2POD_DEV ? 4 : 3)
#define USB_ROW_PROP "_usb_row"

static int usb_chooser_closed(void *ctx, void *event) {
    if (u.chooser == ctx) {
        u.chooser = u.view = u.always = (void *)0;
        if (!u.chosen && g_usbdet_value) usb_apply(USB_CHARGE, 0); /* Return: charge only */
    }
    return 0;
}

static int usb_row_click(void *ctx, void *event) {
    int i = (int)(long)ctx, always = u.always && widget_get_prop_int(u.always, "_on", 0);
    if (i < 0 || i >= USB_ROWS) return 0;
    u.chosen = 1;
    usb_apply(USB_CHARGE + i, always);
    if (i + USB_CHARGE != USB_DEBUG) usb_page_close(&u.chooser);
    return 0;
}

static int usb_always_click(void *ctx, void *event) {
    int on = !widget_get_prop_int(u.always, "_on", 0);
    widget_set_prop_int(u.always, "_on", on);
    list_toggle(u.always, "Always Do This", on);
    return 0;
}

/* The rows at their heights for the selection: the selected one tall with its caption, the switch
 * row USB_GAP lower. The list view leaves its items where they are put (item_height 0). */
static void usb_layout(void) {
    int sel = widget_get_prop_int(u.view, "_ringnav_index", 1), y = 0;
    if (sel == u.row_sel) return;
    u.row_sel = sel;
    for (int i = 0, n = widget_count_children(u.view); i < n; ++i) {
        void *row = widget_get_child(u.view, i);
        int k = widget_get_prop_int(row, USB_ROW_PROP, -1);
        int h = k == sel && k < USB_ROWS ? USB_ROW_TALL : USB_ROW;
        if (k == USB_ROWS) y += USB_GAP;
        widget_move_resize(row, 0, y, 375, h);
        void *label = widget_get_child(row, 0);
        if (label) widget_move_resize(label, I(label, W_X), 0, I(label, W_W), USB_ROW);
        y += h;
    }
    widget_set_prop_int(u.view, "virtual_h", y);
    void *list = P(u.view, W_PARENT);
    widget_resize(list, 375, y);
    widget_resize(u.view, 375, y);
    widget_invalidate_force(u.chooser, (void *)0);
}

static void usb_chooser_open(void) {
    if (u.chooser || !(u.chooser = page_open("usbchoose_page", usb_chooser_closed, page_keyup))) return;
    awake_screen();
    u.chosen = 0, u.row_sel = -1, u.view_ready = 0;
    void *view = u.view = page_list(u.chooser, u.chooser, 0, "Use USB For", USB_ROWS + 1, USB_ROW);
    for (int i = 0; i < USB_ROWS; ++i) {
        void *row = page_row_detail(view, i, usb_names[i], 0, usb_row_click);
        widget_set_prop_int(row, USB_ROW_PROP, i);
        void *label = widget_get_child(row, 0);
        widget_move_resize(label, USB_TEXT_X, 0, 375 - USB_TEXT_X - geom_val(V_RIGHT_X), USB_ROW);
    }
    void *row = page_row_detail(view, USB_ROWS, "Always Do This", 0, usb_always_click);
    widget_set_prop_int(row, USB_ROW_PROP, USB_ROWS);
    u.always = widget_get_child(row, 0);
    list_toggle(u.always, "Always Do This", 0);
    widget_set_prop_int(P(view, W_PARENT), "item_height", 0);
    ringnav_select(view, 1, USB_ROWS + 1); /* Transfer Files */
    usb_layout();
}

/* ---- the mode pages ---- */

static int usb_debug_closed(void *ctx, void *event) {
    if (u.debug == ctx) u.debug = (void *)0;
    return 0;
}
static int usb_debug_key(void *ctx, void *event) {
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key != KEY_RETURN && key != KEY_CENTER) return 0;
    usb_page_close(&u.debug);
    if (key == KEY_CENTER) { /* Stop: the ADB gadget off the controller until the cable is out */
        system("/usr/bin/q2adb-gadget park >/dev/null 2>&1");
        u.choice = USB_CHARGE;
    }
    return STOP;
}
static int usb_debug_open(void) {
    if (!u.debug) {
        u.debug = page_open("usbdebug_page", usb_debug_closed, usb_debug_key);
        if (u.debug) widget_on(u.debug, EVT_KEY_UP, usb_debug_key, 0); /* Centre too */
    }
    usb_page_close(&u.chooser);
    return 0;
}

static int usb_mode_gone(void *ctx, void *event) {
    if (ctx == u.mode) u.mode = (void *)0;
    return 0;
}

/* stock's usbmode_page as it loads (navigation.c ringnav_ui_load): its pictures, line and the DAC's
 * switches hidden (each keeps its binding: the DAC's charging switch is in Settings > Power too);
 * the page is painted as a mode page (usb_ui_paint). */
static void usb_mode_hide(void *win) {
    static const char *const hide[] = { "img_usbmode", "scrlabel_info", "img_daccharge", "img_dacset" };
    void *c;
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1)) && widget_get_visible(c)) widget_set_visible(c, 0, 0);
    /* the back arrow unseen but there: stock's Return clicks it to leave the mode */
    if ((c = widget_lookup(win, "img_return", 1)) && widget_get_prop_int(c, "_usb_unseen", 0) == 0) {
        widget_set_prop_int(c, "_usb_unseen", 1);
        widget_set_opacity(c, 0);
    }
}
void usb_ui_mode_load(void *win) {
    if (tk_strcmp(widget_get_prop_str(win, "name", ""), "usbmode_page")) return;
    usb_mode_hide(win);
    u.mode = win, u.ejected = u.ejected_told = 0;
    widget_on(win, EVT_DESTROY, usb_mode_gone, win);
    if (g_usbmode == USB_MODE_DAC && !u.chosen) usb_toast("usbtoast_audio", "USB Audio Ready");
}

/* navigation.c usb_poll: the computer ejected the card. */
void usb_ui_ejected(void *win) {
    u.ejected = 1;
    if (win != window_manager_get_top_window(window_manager()) && !u.ejected_told) {
        u.ejected_told = 1;
        usb_toast("usbtoast_ejected", "OK to Disconnect");
    }
    widget_invalidate_force(win, (void *)0);
}

/* The status row's title on a USB page (navigation.c's bar titles); 0 for another page. */
const char *usb_ui_title(const char *name) {
    if (!tk_strcmp(name, "usbmode_page")) return g_usbmode == USB_MODE_DAC ? "USB Audio" : "Transfer Files";
    if (!tk_strcmp(name, "usbdebug_page")) return "USB Debugging";
    return (const char *)0;
}

/* The ringnav keyup gate: on usbmode_page Centre is its Stop, a click on stock's back arrow (unseen,
 * usb_mode_hide), which leaves the mode. */
int usb_ui_key(void *top, unsigned key) {
    if (key != KEY_CENTER || !top || top != u.mode) return 0;
    void *ret = widget_lookup(top, "img_return", 1);
    if (!ret) return 0;
    char click[0x30];
    stock_dispatch_trampoline(ret, pointer_event_init(click, EVT_CLICK, ret, 0, 0));
    return 1;
}

static void usb_text(void *canvas, const char *s, int x, int w, int y, int align, const char *face, int px,
                     unsigned rgb) {
    unsigned buf[64], n = ar_wide(s, buf, 64);
    sp_text(canvas, buf, n, x, w, y, align, face, px, RGBA(rgb));
}
static int usb_measure(void *canvas, const char *s, const char *face, int px) {
    unsigned buf[64], n = ar_wide(s, buf, 64);
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, buf, n) + 0.5f);
}

/* A mode page in w's coordinates (dy its y on screen). */
static void usb_mode_paint(void *w, void *canvas, int dy, unsigned ring, const char *glyph, const char *title,
                           const char *line, const char *pill) {
    int light = ipod_light(), stack = pill ? MODE_STACK_PILL : MODE_STACK;
    int top = MODE_TOP + (320 - MODE_TOP - stack) / 2 - dy, cx = 375 / 2;
    rect_t circle = { cx - MODE_R, top, 2 * MODE_R, 2 * MODE_R };
    fill_box(canvas, &circle, RGBA(ring), MODE_R);
    paint_art(w, canvas, theme_art(glyph), cx - MODE_GLYPH / 2, top + MODE_R - MODE_GLYPH / 2);
    usb_text(canvas, title, 0, 375, top + MODE_TITLE_Y, 1, "inter-medium", MODE_TITLE_PX,
             light ? 0x000000 : 0xffffff);
    usb_text(canvas, line, 0, 375, top + MODE_LINE_Y, 1, "inter", MODE_LINE_PX, light ? 0x5c5c5c : 0xababab);
    if (pill) {
        int pw = usb_measure(canvas, pill, "inter-medium", MODE_PILL_PX) + 2 * MODE_PILL_PAD;
        rect_t r = { cx - pw / 2, top + MODE_PILL_Y, pw, MODE_PILL_H };
        fill_box(canvas, &r, RGBA(light ? 0xd1d1d6 : MODE_PILL_RGB), MODE_PILL_H / 2);
        usb_text(canvas, pill, r.x, r.w, r.y + MODE_PILL_H / 2, 1, "inter-medium", MODE_PILL_PX,
                 light ? 0x000000 : 0xffffff);
    }
}

/* A page opened from a poll, not from a key: once it is on top and settled, the wheel's list is
 * loaded on it (ringnav_select), so the first Centre presses its row. */
static void usb_ready(void *page, void *view, int *ready, int rows) {
    void *wm = window_manager();
    if (*ready || window_manager_get_top_window(wm) != page || window_manager_is_animating(wm)) return;
    *ready = 1;
    ringnav_select(view, widget_get_prop_int(view, "_ringnav_index", 0), rows);
}

/* ringnav_paint, after w's children: the chooser's glyphs and caption, the mode pages, the ADB
 * prompt's key and note. */
static void usb_auth_paint(void *w, void *canvas);
void usb_ui_paint(void *w, void *canvas) {
    void *lcd = w ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || (!u.chooser && !u.debug && !u.mode && !u.auth)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR), raw = st.raw_ink;
    void *parent = P(w, W_PARENT);
    unsigned accent = scheme()->tones[0] & 0xffffffu;
    st.raw_ink = 1;
    if (w == u.mode && !window_manager_is_animating(window_manager())) {
        int dac = g_usbmode == USB_MODE_DAC;
        usb_mode_hide(w); /* stock's face for the mode shows the DAC's switches again */
        if (u.ejected && !dac)
            usb_mode_paint(w, canvas, I(w, W_Y), MODE_GREEN, "usbmode_ejected", "OK to Disconnect",
                           "Safe to unplug now", (const char *)0);
        else if (dac)
            usb_mode_paint(w, canvas, I(w, W_Y), accent, "usbmode_audio", "Playing from Computer",
                           "Wheel sets the volume", "Stop");
        else
            usb_mode_paint(w, canvas, I(w, W_Y), accent, "usbmode_files", "Connected",
                           "Eject on your computer first", "Stop");
    } else if (w == u.debug) {
        usb_mode_paint(w, canvas, I(w, W_Y), accent, "usbmode_debug", "Debugging", "Charging and connected", "Stop");
    } else if (u.view && parent == u.view) {
        usb_ready(u.chooser, u.view, &u.view_ready, USB_ROWS + 1);
        usb_layout();
        int i = widget_get_prop_int(w, USB_ROW_PROP, -1), sel = ringnav_row_selected(w);
        if (i >= 0 && i < USB_ROWS) {
            char glyph[24];
            tk_snprintf(glyph, sizeof glyph, "%s_%c", usb_glyphs[i], sel ? 'w' : ipod_light() ? 'l' : 'd');
            paint_art(w, canvas, theme_art(glyph), USB_ICON_X - USB_ICON / 2 - I(canvas, CANVAS_X),
                      USB_ROW / 2 - USB_ICON / 2);
            if (sel)
                usb_text(canvas, usb_caps[i], USB_TEXT_X - I(canvas, CANVAS_X), 375 - USB_TEXT_X, USB_CAP_Y, 2,
                         "inter", USB_CAP_PX, USB_CAP_RGB);
        }
    } else if (w == u.auth) {
        if (u.auth_view) usb_ready(u.auth, u.auth_view, &u.auth_ready, 3);
        usb_auth_paint(w, canvas);
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* ---- the plug, from check_usb_status (usb_charge.c ringnav_usb_status) ---- */

/* Before stock's pass: a cable in or out, and the probe's answer. */
void usb_ui_status(void) {
    int det = g_usbdet_value == 1;
    if (det && !u.det) charge_plugged(); /* charging.c: a dark Q2 lights for it */
    if (det && !u.det && g_usblink_status == 0) {
        u.choice = USB_ASK, u.chosen = 0;
        int pick = usb_remembered();
        if (pick == USB_FILES || pick == USB_AUDIO) {
            u.choice = pick; /* stock's own path, in the tile's mode */
        } else {
            g_usblink_status = USB_LINK_HELD;
            if (pick == USB_ASK) {
                system("storage_config.sh start /dev/null >/dev/null 2>&1"); /* no medium: a probe */
                u.probing = 1, u.probe_at = (unsigned)time_now_ms();
            } else {
                u.choice = pick;
                usb_charger_in();
            }
        }
    } else if (!det && u.det) {
        u.probing = 0;
        usb_page_close(&u.chooser);
        usb_page_close(&u.debug);
        if (u.mode_saved) g_usbmode = (unsigned char)(u.mode_saved - 1);
        u.mode_saved = 0, u.choice = USB_ASK;
        usb_toast("usbtoast_unplugged", "Unplugged");
    }
    u.det = det;
    if (u.probing && det) {
        if (usb_host()) {
            u.probing = 0, u.chosen = 0;
            usb_chooser_open();
        } else if ((unsigned)time_now_ms() - u.probe_at > USB_PROBE_MS) {
            u.probing = 0;
            system("storage_config.sh stop >/dev/null 2>&1");
            usb_charger_in();
        }
    }
    if (det && u.choice == USB_DEBUG && !u.debug && !u.chooser && usb_host() && !u.chosen) {
        u.chosen = 1;
        usb_debug_open(); /* remembered: once a computer is there */
    }
}


/* ---- the ADB prompt (--dev builds) ---- */

#if Q2POD_DEV
static int b64(int c) {
    return c >= 'A' && c <= 'Z' ? c - 'A' : c >= 'a' && c <= 'z' ? c - 'a' + 26 : c >= '0' && c <= '9' ? c - '0' + 52
           : c == '+' ? 62 : c == '/' ? 63 : -1;
}
/* The key's fingerprint as Android shows it, the MD5 of the decoded key, its first eight bytes. */
static void auth_fingerprint(void) {
    static unsigned char raw[600];
    unsigned char md[16];
    unsigned n = 0, acc = 0, bits = 0;
    for (const char *p = u.key; *p && *p != ' ' && n < sizeof raw; ++p) {
        int v = b64((unsigned char)*p);
        if (v < 0) continue;
        acc = acc << 6 | (unsigned)v, bits += 6;
        if (bits >= 8) raw[n++] = (unsigned char)(acc >> (bits -= 8));
    }
    unsigned ctx[24];
    MD5_Init(ctx);
    MD5_Update(ctx, raw, n);
    MD5_Final(md, ctx);
    for (int i = 0; i < 8; ++i) tk_snprintf(u.fp + 3 * i, 4, i < 7 ? "%02X:" : "%02X", md[i]);
}

static void auth_answer(const char *a) {
    if (u.fd >= 0) send(u.fd, a, 2, 0);
}
static int auth_closed(void *ctx, void *event) {
    if (u.auth == ctx) {
        u.auth = u.auth_view = u.auth_switch = (void *)0;
        if (!u.got) auth_answer("NO"); /* Return: Cancel */
    }
    return 0;
}
static int auth_click(void *ctx, void *event) {
    int allow = (int)(long)ctx;
    u.got = 1;
    if (allow && u.auth_always) { /* Always Allow This Computer: adbd reads the file before it lets it in */
        void *f = fopen(AUTH_KEYS, "ab");
        if (f) {
            fwrite(u.key, 1, strlen(u.key), f);
            fwrite("\n", 1, 1, f);
            fclose(f);
        }
    }
    auth_answer(allow ? "OK" : "NO");
    usb_page_close(&u.auth);
    return 0;
}
static int auth_switch_click(void *ctx, void *event) {
    u.auth_always = !u.auth_always;
    list_toggle(u.auth_switch, "Always Allow This Computer", u.auth_always);
    return 0;
}
static void auth_open(void) {
    if (u.auth) usb_page_close(&u.auth);
    if (!(u.auth = page_open("adbauth_page", auth_closed, page_keyup))) return;
    awake_screen();
    u.got = 0, u.auth_always = 0, u.auth_ready = 0;
    auth_fingerprint();
    void *view = u.auth_view = page_list(u.auth, u.auth, 0, "Allow USB Debugging?", 3, 32);
    void *list = P(view, W_PARENT);
    widget_move_resize(list, I(list, W_X), I(list, W_Y) + AUTH_HEAD, I(list, W_W), I(list, W_H));
    page_row_detail(view, 0, "Cancel", 0, auth_click);
    widget_on(page_row_detail(view, 1, "Allow", 0, (int (*)(void *, void *))0), EVT_CLICK, auth_click, (void *)1);
    u.auth_switch = widget_get_child(page_row_detail(view, 2, "Always Allow This Computer", 0, auth_switch_click), 0);
    list_toggle(u.auth_switch, "Always Allow This Computer", 0);
    ringnav_select(view, 0, 3); /* Cancel */
}
static void usb_auth_paint(void *w, void *canvas) {
    int dy = I(w, W_Y), light = ipod_light(), x = geom_val(V_TEXT_X);
    void *list = u.auth_view ? P(u.auth_view, W_PARENT) : (void *)0;
    int top = list ? I(list, W_Y) - AUTH_HEAD : 58 - dy;
    usb_text(canvas, "This computer's key:", x, 375 - 2 * x, top + 12, 2, "inter", MODE_LINE_PX,
             light ? 0x5c5c5c : 0xababab);
    usb_text(canvas, u.fp, x, 375 - 2 * x, top + 34, 2, "inter-medium", MODE_PILL_PX, light ? 0x000000 : 0xffffff);
    if (list)
        usb_text(canvas, "Gives full control of the Q2.", x, 375 - 2 * x, I(list, W_Y) + I(list, W_H) + 20, 2,
                 "inter", AUTH_NOTE_PX, light ? 0x6e6e6e : 0x858585);
}

/* adbd's UI socket, connected once a second until it is there, and its questions. */
static void auth_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    if (u.fd <= 0) {
        if (now - u.auth_at < 1000) return;
        u.auth_at = now;
        int fd = socket(1 /* AF_UNIX */, 1 /* SOCK_STREAM */, 0);
        struct {
            unsigned short family;
            char path[108];
        } a = { 1, AUTH_SOCKET };
        if (fd < 0) return;
        if (connect(fd, &a, sizeof a)) {
            close(fd);
            return;
        }
        fcntl(fd, 4 /* F_SETFL */, 0x80 /* O_NONBLOCK */);
        u.fd = fd, u.got = 0;
        return;
    }
    int n = (int)recv(u.fd, u.in, sizeof u.in - 1, 0);
    if (n == 0 || (n < 0 && *__errno_location() != 11 /* EAGAIN */)) {
        close(u.fd);
        u.fd = 0;
        usb_page_close(&u.auth);
        return;
    }
    if (n < 3 || u.in[0] != 'P' || u.in[1] != 'K') return;
    u.in[n] = 0;
    tk_snprintf(u.key, sizeof u.key, "%s", u.in + 2);
    auth_open();
}
#else
static void usb_auth_paint(void *w, void *canvas) {}
static void auth_poll(void) {}
#endif

/* ringnav_sleep: a toast usb_ui_status left, charging's, and adbd's questions. */
void usb_ui_poll(void) {
    if (u.toast_text) {
        const char *glyph = u.toast_glyph, *text = u.toast_text;
        u.toast_glyph = u.toast_text = (const char *)0;
        if (g_backlight_status) toast_icon(theme_art(glyph), text);
    }
    charging_poll(); /* charging.c's toasts */
    auth_poll();
}
