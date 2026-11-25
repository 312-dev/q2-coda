/* The USB pages (docs/internals.md#usb-pages, docs/oled-design.md#usb): what a cable into a computer
 * shows. Stock starts a gadget the moment a cable is in (check_usb_status, hooked as
 * ringnav_usb_status in usb_charge.c, which calls usb_ui_status around it) and, once a computer
 * configures it, opens usbmode_page in the mode Quick Settings' USB tile last chose. Here:
 *
 * - A cable in on Automatic, the setting's default, keeps stock out (g_usblink_status 3, which its next
 *   pass leaves alone until the cable comes out) and shows the host an iPhone (usb_car.c): a car keeps it
 *   and plays the Q2 over USB (Car Audio); any other host gets Transfer Files (usb_car_storage); a charger
 *   only charges. USB Debugging is never reached this way, only by picking it.
 * - A cable in on Ask Every Time keeps stock out the same way and starts stock's storage gadget with no card in it
 *   (a reader with no medium), only to learn whether a computer answers. One that configures it
 *   within USB_PROBE_MS gets the chooser, "Use USB For": Charge Only, Transfer Files, USB Audio and,
 *   in a --dev build, USB Debugging, and Always Do This. Nothing answering is a charger: the probe
 *   stops and usb_charger_in (charging.c) toasts the charge. A cable in at all tells charging.c
 *   (charge_plugged), whose plug-in screen lights a dark Q2.
 * - Transfer Files and USB Audio hand back to stock: the probe stops, g_usbmode is set for this
 *   cable (stock's saved one, SYSSET USBMODE, again once it is out, unless Always Do This saved it) and
 *   g_usblink_status 0 lets stock's next pass start its gadget and open usbmode_page, which is
 *   drawn here as a mode page: one solid circle and its glyph, a title, a line and a Stop button
 *   (Centre, as Return, leaves the mode). Charge Only stops the probe. USB Debugging stops it too,
 *   which in a --dev build hands the controller back to the ADB gadget (docs/adb.md), and opens
 *   its own mode page, whose Stop parks the ADB gadget. A --dev build whose ADB gadget is already
 *   on the controller probes with it (usb_adb_bound): no storage gadget, nothing to stop.
 * - The cable is seen in the UI pass after its edge (usb_event.c, usb_ui_poll), and a cable out
 *   stops the probe's gadget, answered or not (docs/internals.md#usb-cable-events).
 * - A remembered choice skips the chooser, Transfer Files and USB Audio in their own mode for the
 *   cable, and holds until a mode page's Stop forgets it (back to Automatic) or the USB picker
 *   changes it.
 * - The USB picker, "USB" (usb_pick_open: Settings > General's USB row, usb_ui_row, and Control
 *   Center's while a cable is in): Automatic, Transfer Files, USB Audio, Charge Only, Ask Every Time
 *   and, in a --dev build, USB Debugging, the remembered one checked and what it does under the list. A pick
 *   is remembered; with a cable in it is that cable's mode at once (usb_switch), but leaving
 *   Transfer Files while the computer has the card asks first.
 * - Toasts: Unplugged, OK to Disconnect (the computer's eject while USB mode is not on top) and
 *   USB Audio Ready (the DAC mode reached without the chooser).
 * - --dev builds: adbd's question about an unknown computer (docs/adb.md#authorization) from its
 *   socket, asked on adb_trust.c's card, "Allow this computer?", and its answer sent back. */
#include "navigation.h"

#define USB_PROBE_MS 3000 /* a computer configures a gadget within a second or two */
#define USB_PROBE_LOOK_MS 100 /* how often the waiting probe reads the UDC's state */
#define USB_UDC "/sys/class/udc/13500000.otg/state"
#define USB_ADB_UDC "/sys/kernel/config/usb_gadget/q2adb/UDC" /* --dev: the ADB gadget's controller */
#define USB_PICK_KEY "USBPICK" /* [Q2POD]: the remembered choice, USB_* */
/* The USB setting's values (USBPICK); 0, Automatic, is also the setting when nothing was ever picked */
enum { USB_AUTO, USB_CHARGE, USB_FILES, USB_AUDIO, USB_DEBUG, USB_ASK, USB_CHOICES };
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
#define USB_CAP_PX TYPE(15) /* Inter Regular 15 */
#define USB_CAP_RGB RGB_CAPTION_ON_ACCENT

/* The ADB prompt (U5): adbd's socket and a key's longest; the card is adb_trust.c's. */
#define AUTH_SOCKET "/tmp/q2adb-ui" /* tools/adbd.patch adb_auth_client.c ADB_UI_SOCKET */
#define AUTH_KEY_MAX 1024

static struct {
    int view_ready;
    int det_known; /* a pass has read the cable: one in at boot is no plug-in */
    int det, probing, probe_up /* the probe's storage gadget is running */, choice, mode_saved /* g_usbmode + 1 for this cable's, 0 none */, ejected, ejected_told, chosen, row_sel;
    unsigned probe_at, probe_look_at, plug_at;
    int by_event;                    /* this pass's cable change came from usb_event.c's edge */
    unsigned long long edge_ns;      /* ... and that edge */
    void *chooser, *view, *always, *debug, *mode;
    /* the USB picker, its footer, General's row's label, and a pick the confirmation card is asking about */
    void *pick, *pick_view, *pick_foot, *row;
    int pending;
    int after_car; /* a pick + 1 waiting for Automatic's iPhone to be off the controller (usb_switch), 0 none */
    /* the ADB prompt */
    int fd;
    unsigned auth_at;
    char in[AUTH_KEY_MAX + 8], key[AUTH_KEY_MAX];
} u __attribute__((section(".scratch")));

/* 1 while a host has configured the gadget on the controller (system.c holds Standby on it too) */
int usb_host(void) {
    char s[16] = { 0 };
    void *f = fopen(USB_UDC, "rb");
    if (!f) return 0;
    fread(s, 1, sizeof s - 1, f);
    fclose(f);
    return !memcmp(s, "configured", 10);
}

/* --dev builds: the ADB gadget is on the controller (patch/adb_gadget.sh bind), so a computer at the
 * cable sees it at once and its answer is the probe's: no storage gadget swapped in and out. */
static int usb_adb_bound(void) {
    if (!Q2POD_DEV) return 0;
    char s[8] = { 0 };
    void *f = fopen(USB_ADB_UDC, "rb");
    if (!f) return 0;
    fread(s, 1, sizeof s - 1, f);
    fclose(f);
    return s[0] > ' ';
}

/* A toast, shown by the next loop pass (feedback.c toast_later): never inside stock's USB pass,
 * whose own loop a toast's would run again. */
static void usb_toast(const char *glyph, const char *text) {
    if (g_backlight_status) toast_later(glyph, text);
}

/* The USB setting, USB_AUTO unless something else was picked (USB Debugging's is Automatic in a release
 * build). config_value takes a value under its count, so the read names the values it may hold. */
static int usb_remembered(void) {
    int pick = config_value("Q2POD", USB_PICK_KEY, USB_CHOICES, USB_AUTO);
    return pick <= USB_AUTO || (pick == USB_DEBUG && !Q2POD_DEV) ? USB_AUTO : pick;
}

static const char *const usb_pick_names[USB_CHOICES] = { "Automatic",     "Charge Only",   "Transfer Files",
                                                         "USB Audio",     "USB Debugging", "Ask Every Time" };
/* The remembered choice's name: General's USB row's value and Control Center's USB row's. */
const char *usb_ui_value(void) { return usb_pick_names[usb_remembered()]; }
/* A cable in, as stock's pass reads it: Control Center's USB row shows only then. */
int usb_ui_cable(void) { return g_usbdet_value == 1; }

/* General's USB row (nav/settings_display.c ringnav_systemset): its value, the remembered choice,
 * written again by every change while the row is there. */
static void usb_row_text(void) {
    if (u.row) list_value(u.row, "USB", usb_ui_value());
}
static int usb_row_gone(void *label, void *event) {
    if (u.row == label) u.row = (void *)0;
    return 0;
}
void usb_ui_row(void *label) {
    u.row = label;
    widget_on(label, EVT_DESTROY, usb_row_gone, label);
    usb_row_text();
}

/* A mode page's Stop: the mode it stops, if the setting holds it, back to Automatic for the next cable.
 * Automatic and Ask Every Time are not a mode, and stay. */
static void usb_forget(void) {
    int pick = config_value("Q2POD", USB_PICK_KEY, USB_CHOICES, USB_AUTO);
    if (pick != USB_AUTO && pick != USB_ASK) write_int_config(USB_AUTO, "Q2POD", USB_PICK_KEY);
    usb_row_text();
}

/* pick remembered for every cable (Always Do This, or the USB picker): Transfer Files and USB Audio
 * with stock's own mode (SYSSET USBMODE), as its USB setting saved it, so no cable's mode is put
 * back when this one is out. */
static void usb_remember(int pick) {
    if (pick != USB_AUTO || config_value("Q2POD", USB_PICK_KEY, USB_CHOICES, USB_AUTO) != USB_AUTO)
        write_int_config(pick, "Q2POD", USB_PICK_KEY);
    if (pick == USB_FILES || pick == USB_AUDIO) {
        g_usbmode = pick == USB_AUDIO ? USB_MODE_DAC : USB_MODE_FILES;
        write_int_config(g_usbmode, "SYSSET", "USBMODE"); /* stock's own */
        u.mode_saved = 0;
    }
    usb_row_text();
}

/* g_usbmode for this cable; stock's saved one comes back when it is out. */
static void usb_mode(int audio) {
    if (!u.mode_saved) u.mode_saved = g_usbmode + 1; /* 0: none saved */
    g_usbmode = audio ? USB_MODE_DAC : USB_MODE_FILES;
}

/* A line in the unified log (ulog.c, docs/internals.md#unified-log): each plug and unplug, how it was
 * seen and how long after its edge, and a computer's answer to the probe
 * (docs/internals.md#usb-cable-events). */
static void usb_log(const char *what, int ms) {
    char line[96];
    if (ms < 0)
        ulog('I', "usb", what);
    else if (tk_snprintf(line, sizeof line, "%s, %d ms", what, ms) > 0)
        ulog('I', "usb", line);
}

/* The probe's storage gadget stopped, if it is running. */
static void usb_probe_stop(void) {
    if (u.probe_up) system("storage_config.sh stop >/dev/null 2>&1");
    u.probe_up = 0;
}

static void usb_page_close(void **page) {
    void *p = *page;
    *page = (void *)0;
    if (p) window_manager_close_window_force(window_manager(), p);
}

static int usb_debug_open(void);

/* A choice made for the cable that is in (the chooser's, a remembered one's, the USB picker's);
 * always remembers it too. */
static void usb_apply(int pick, int always) { /* always 2: remembered already (usb_switch) */
    u.choice = pick;
    usb_probe_stop();
    u.probing = 0;
    if (always == 1) usb_remember(pick);
    if (pick == USB_FILES || pick == USB_AUDIO) {
        if (!always) usb_mode(pick == USB_AUDIO);
        g_usblink_status = 0; /* stock's next pass starts its gadget and opens usbmode_page */
    } else if (pick == USB_DEBUG) {
        usb_debug_open();
    }
}

/* ---- the chooser ---- */

static const char *const *const usb_names = usb_pick_names + USB_CHARGE; /* its rows' order */
static const char *const usb_caps[] = { "Keep listening while it charges", "Your computer opens the SD card",
                                        "Play your computer's sound here", "Developer builds only" };
static const char *const usb_glyphs[] = { "usb_charge", "usb_files", "usb_audio", "usb_debug" };
#define USB_ROWS (Q2POD_DEV ? 4 : 3)
#define USB_ROW_PROP "_usb_row"

static int usb_chooser_closed(void *ctx, void *event) {
    if (u.chooser == ctx) {
        u.chooser = u.view = u.always = (void *)0;
        if (!u.chosen && g_usbdet_value) { /* Return: charge only, the probe's gadget stopped as a row's */
            u.chosen = 1;
            usb_apply(USB_CHARGE, 0);
        }
    }
    return 0;
}

static int usb_row_click(void *ctx, void *event) {
    int i = (int)(long)ctx, always = u.always && widget_get_prop_int(u.always, "_on", 0);
    if (i < 0 || i >= USB_ROWS) return 0;
    u.chosen = 1;
    usb_apply(USB_CHARGE + i, always != 0);
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
        usb_forget();
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

/* stock's usbmode_page gone: its exit (usbmode_exit, on EVT_WINDOW_CLOSE) has stopped the gadget and
 * holds the cable (g_usblink_status 3) until it is out, so it only charges now. */
static int usb_mode_gone(void *ctx, void *event) {
    if (ctx == u.mode) u.mode = (void *)0, u.choice = USB_CHARGE;
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
 * usb_mode_hide), which leaves the mode and forgets a remembered choice. Once the computer has
 * ejected the card the page has no Stop (OK to Disconnect): Centre only leaves. On a mode page the
 * wheel shows Stop's focus ring (docs/oled-design.md#focus-ring) and goes on to stock, whose
 * USB Audio turns the volume with it. */
int usb_ui_key(void *top, unsigned key) {
    if ((key == KEY_NEXT || key == KEY_PREV) && top && (top == u.mode || top == u.debug)) {
        if (st.touch_mode) st.touch_mode = 0, widget_invalidate_force(top, (void *)0);
        return 0;
    }
    if (key != KEY_CENTER || !top || top != u.mode) return 0;
    void *ret = widget_lookup(top, "img_return", 1);
    if (!ret) return 0;
    if (!u.ejected || g_usbmode == USB_MODE_DAC) usb_forget();
    char click[0x30];
    stock_dispatch_trampoline(ret, pointer_event_init(click, EVT_CLICK, ret, 0, 0));
    return 1;
}

/* ---- the USB picker ---- */

/* Its rows' choices, top down, and the note under the list for the checked one (two lines at most,
 * USB_FOOT_DY and USB_FOOT_LINE: design/usb-row mock_usb_row.py, 13 px Text Tertiary). */
static const unsigned char usb_pick_order[] = { USB_AUTO, USB_FILES, USB_AUDIO, USB_CHARGE, USB_ASK, USB_DEBUG };
#define USB_PICK_ROWS (Q2POD_DEV ? 6 : 5)
#define USB_PICK_TICK "_usb_tick" /* a row's check image: stock's select, painted as the theme's check */
#define USB_TICK_W 24
#define USB_FOOT_H 48
#define USB_FOOT_DY 19   /* the first line's centre under the rows */
#define USB_FOOT_LINE 17 /* and the next one's under it */
#define USB_FOOT_PX TYPE(13)
static const char *const usb_notes[USB_CHOICES][2] = {
    { "A car plays the Q2 through its speakers.", "A computer opens the SD card." },
    { "Every cable only charges, a computer's too.", 0 },
    { "A computer opens the SD card on every cable.", "Stop on its page goes back to Automatic." },
    { "A computer plays its sound here on every cable.", "Stop on its page goes back to Automatic." },
    { "A computer gets adb on every cable.", "Stop on its page goes back to Automatic." },
    { "Plug into a computer to choose.", 0 },
};

/* The cable's mode now: Automatic's while its iPhone is shown, stock's mode page's, USB Debugging's page's, else
 * the choice it got. */
static int usb_now(void) {
    if (usb_car_on()) return USB_AUTO;
    if (u.mode) return g_usbmode == USB_MODE_DAC ? USB_AUDIO : USB_FILES;
    return u.debug ? USB_DEBUG : u.choice;
}

/* The pick made once what the cable had is let go (usb_switch): remember 1, or 2 when it already was. */
static void usb_switch_to(int now, int pick, int remember) {
    usb_apply(pick, remember);
    if (now == USB_DEBUG && pick == USB_CHARGE)
        system("/usr/bin/q2adb-gadget park >/dev/null 2>&1");
    else if (pick == USB_DEBUG)
        system("/usr/bin/q2adb-gadget unpark >/dev/null 2>&1");
}

/* The cable that is in, switched to pick and pick remembered. What it had is let go first: stock's
 * mode by closing its page, whose EVT_WINDOW_CLOSE runs stock's usbmode_exit (the gadget stopped,
 * the card mounted again, g_usblink_status 3; demo 0x542ad0); stock's gadget still waiting for a
 * computer by holding the link and stopping it; USB Debugging's page closed, its gadget parked for
 * Charge Only. USB Debugging unparks its gadget, which a stop of stock's gadget may have left parked
 * (patch/adb_gadget.sh). */
static void usb_switch(int pick) {
    int now = usb_now();
    if (pick == now) {
        usb_remember(pick);
        return;
    }
    if (u.chooser) {
        u.chosen = 1; /* answered: its Return's Charge Only does not follow */
        usb_page_close(&u.chooser);
    }
    if (u.mode) {
        usb_page_close(&u.mode);
    } else if ((now == USB_FILES || now == USB_AUDIO) && g_usblink_status != USB_LINK_HELD) {
        g_usblink_status = USB_LINK_HELD;
        system(now == USB_AUDIO ? "uac_device_config.sh stop >/dev/null 2>&1"
                                : "storage_config.sh stop >/dev/null 2>&1");
    }
    usb_page_close(&u.debug);
    usb_car_stop(); /* Automatic's iPhone off the controller before the pick's gadget */
    if (usb_car_busy()) {
        /* its stop waits for the player's release and runs in the background (usb_car.c): the pick is remembered
         * now and made by the first UI pass after it (usb_ui_poll) */
        usb_remember(pick);
        u.after_car = pick + 1;
        return;
    }
    usb_switch_to(now, pick, 1);
}

/* Each row's check, the remembered choice's, and its note under the list. */
static void usb_pick_marks(void) {
    int now = usb_remembered();
    for (int i = 0; u.pick_view && i < USB_PICK_ROWS; ++i) {
        void *item = widget_get_child(u.pick_view, i);
        void *tick = item ? widget_lookup(item, USB_PICK_TICK, 0) : (void *)0;
        if (tick) image_base_set_image(tick, usb_pick_order[i] == now ? "select" : "unselect");
    }
    if (u.pick_foot) widget_invalidate_force(u.pick_foot, (void *)0);
}

/* pick remembered and, with a cable in, that cable's mode now; Automatic and Ask Every Time are the next
 * cable's. */
static void usb_pick_set(int pick) {
    if (pick == USB_AUTO || pick == USB_ASK || !usb_ui_cable())
        usb_remember(pick);
    else
        usb_switch(pick);
    usb_pick_marks();
}

/* The confirmation card's answer: Switch (row 1) makes the pick it asked about. */
static void usb_pick_answer(void *ctx, int row, int toggle_on) {
    int pick = u.pending;
    u.pending = 0;
    if (row == 1 && pick) usb_pick_set(pick);
}

/* A row's Centre or tap. Leaving Transfer Files while the computer has the card (its mode page up,
 * not ejected) asks first, so a copy in progress is not cut off unasked. */
static int usb_pick_click(void *ctx, void *event) {
    static const char *const rows[] = { "Cancel", "Switch" };
    int i = (int)(long)ctx, pick = i >= 0 && i < USB_PICK_ROWS ? usb_pick_order[i] : -1;
    if (pick < 0) return 0;
    int cable = usb_ui_cable() && pick != USB_AUTO && pick != USB_ASK, now = cable ? usb_now() : -1;
    if (pick == usb_remembered() && (!cable || pick == now)) return 0;
    if (cable && pick != now && u.mode && g_usbmode != USB_MODE_DAC && !u.ejected) {
        u.pending = pick; /* read only by this card's answer; another card up shows none */
        confirm(&(confirm_t){ .title = "Switch to %s?",
                              .name = usb_pick_names[pick],
                              .body = "Eject the SD card on your computer first,\nor a copy in progress stops.",
                              .rows = rows,
                              .n_rows = 2,
                              .kind = CONFIRM_CONSEQUENTIAL,
                              .back = -1,
                              .answer = usb_pick_answer });
        return 0;
    }
    usb_pick_set(pick);
    return 0;
}

static int usb_pick_closed(void *ctx, void *event) {
    if (u.pick == ctx) u.pick = u.pick_view = u.pick_foot = (void *)0;
    return 0;
}

/* The USB picker (General's USB row, Control Center's): one row a choice, the check on the right
 * column, the footer view under the rows. It opens on the checked row (contexts.inc CHOICE) and
 * stays after a pick, as the other pickers do. */
int usb_pick_open(void *ctx, void *event) {
    if (u.pick || !(u.pick = page_open("usbpick_page", usb_pick_closed, page_keyup))) return 0;
    void *view = u.pick_view = page_list(u.pick, u.pick, 0, "USB", USB_PICK_ROWS, 48);
    int right = 375 - geom_val(V_RIGHT_X);
    for (int i = 0; i < USB_PICK_ROWS; ++i) {
        void *item = page_row_detail(view, i, usb_pick_names[usb_pick_order[i]], 0, usb_pick_click);
        void *label = widget_get_child(item, 0);
        int h = I(item, W_H);
        if (label) widget_move_resize(label, I(label, W_X), 0, right - USB_TICK_W - 8 - I(label, W_X), h);
        void *tick = image_create(item, right - USB_TICK_W, 0, USB_TICK_W, h);
        widget_set_name(tick, USB_PICK_TICK);
    }
    void *list = P(view, W_PARENT);
    u.pick_foot = widget_factory_create_widget(widget_factory(), "view", u.pick, 0,
                                               I(list, W_Y) + I(list, W_H), 375, USB_FOOT_H);
    widget_set_name(u.pick_foot, "_usb_pick_foot");
    usb_pick_marks();
    return 0;
}

/* The footer: the checked choice's note, on the text column. */
static void usb_pick_paint(void *w, void *canvas) {
    const char *const *note = usb_notes[usb_remembered()];
    int x = geom_val(V_TEXT_X), cw = 375 - x - geom_val(V_RIGHT_X);
    for (int k = 0; k < 2 && note[k]; ++k)
        ui_line(canvas, note[k], x - page_ox(canvas, w), cw, USB_FOOT_DY + k * USB_FOOT_LINE, 2, "inter",
                USB_FOOT_PX, scheme()->muted);
}

/* A page opened from a poll, not from a key: once it is on top and settled, the wheel's list is
 * loaded on it (ringnav_select), so the first Centre presses its row. */
static void usb_ready(void *page, void *view, int *ready, int rows) {
    void *wm = window_manager();
    if (*ready || window_manager_get_top_window(wm) != page || window_manager_is_animating(wm)) return;
    *ready = 1;
    ringnav_select(view, widget_get_prop_int(view, "_ringnav_index", 0), rows);
}

/* ringnav_paint, after w's children: the chooser's glyphs and caption, the mode pages, the USB
 * picker's footer. */
void usb_ui_paint(void *w, void *canvas) {
    void *lcd = w ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || (!u.chooser && !u.debug && !u.mode && !u.pick_foot)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR), raw = st.raw_ink;
    void *parent = P(w, W_PARENT);
    unsigned accent = scheme()->tones[0] & 0xffffffu;
    st.raw_ink = 1;
    if (w == u.mode && !window_manager_is_animating(window_manager())) {
        int dac = g_usbmode == USB_MODE_DAC;
        usb_mode_hide(w); /* stock's face for the mode shows the DAC's switches again */
        if (u.ejected && !dac)
            mode_page_paint(w, canvas, I(w, W_Y),
                            &(mode_page_t){ RGB_POSITIVE, "modepage_done", 0, "OK to Disconnect",
                                            "Safe to unplug now", (const char *)0 });
        else if (dac)
            mode_page_paint(w, canvas, I(w, W_Y),
                            &(mode_page_t){ accent | MODE_BUTTON, "modepage_audio", 0, "Playing from Computer", "Wheel sets the volume", "Stop" });
        else
            mode_page_paint(w, canvas, I(w, W_Y),
                            &(mode_page_t){ accent | MODE_BUTTON, "modepage_files", 0, "Connected", "Eject on your computer first", "Stop" });
    } else if (w == u.pick_foot) {
        usb_pick_paint(w, canvas);
    } else if (w == u.debug) {
        mode_page_paint(w, canvas, I(w, W_Y),
                        &(mode_page_t){ accent | MODE_BUTTON, "modepage_debug", 0, "Debugging", "Charging and connected", "Stop" });
    } else if (u.view && parent == u.view) {
        usb_ready(u.chooser, u.view, &u.view_ready, USB_ROWS + 1);
        usb_layout();
        int i = widget_get_prop_int(w, USB_ROW_PROP, -1), sel = ringnav_row_selected(w);
        if (i >= 0 && i < USB_ROWS) {
            char glyph[24];
            tk_snprintf(glyph, sizeof glyph, "%s_%c", usb_glyphs[i], sel ? 'w' : ipod_light() ? 'l' : 'd');
            paint_art(w, canvas, theme_art(glyph), USB_ICON_X - USB_ICON / 2 - page_ox(canvas, w),
                      USB_ROW / 2 - USB_ICON / 2);
            if (sel)
                ui_line(canvas, usb_caps[i], USB_TEXT_X - page_ox(canvas, w), 375 - USB_TEXT_X,
                        USB_CAP_Y, 2, "inter", USB_CAP_PX, USB_CAP_RGB);
        }
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* ---- the plug, from check_usb_status (usb_charge.c ringnav_usb_status) ---- */

/* The probe's answer: a computer that configured its gadget gets the chooser; none within
 * USB_PROBE_MS is a charger. From each USB pass, and every USB_PROBE_LOOK_MS from usb_ui_poll while
 * the probe waits, so the chooser follows the computer's answer, not stock's next second. */
static void usb_probe_look(void) {
    if (!u.probing || !u.det) return;
    unsigned now = (unsigned)time_now_ms();
    u.probe_look_at = now;
    if (usb_host()) {
        u.probing = 0, u.chosen = 0;
        usb_log("a computer answered the probe after the plug", (int)(now - u.plug_at));
        usb_chooser_open();
    } else if (now - u.probe_at > USB_PROBE_MS) {
        u.probing = 0;
        usb_probe_stop();
        usb_log("no computer answered: a charger", -1);
        usb_charger_in();
    }
}

/* Before stock's pass: a cable in or out, and the probe's answer. */
void usb_ui_status(void) {
    int det = g_usbdet_value == 1;
    if (det != u.det && u.det_known) {
        usb_record_edge(det); /* the USB record (usb_record.c) */
        if (u.by_event)
            usb_log(det ? "cable in, seen after its edge" : "cable out, seen after its edge",
                    (int)((usb_event_now_ns() - u.edge_ns) / 1000000));
        else
            usb_log(det ? "cable in (stock's once-a-second read)" : "cable out (stock's once-a-second read)", -1);
    }
    if (det && !u.det && u.det_known) {
        charge_plugged(); /* charging.c: a dark Q2 lights for it */
        /* A car's USB coming on with the car (docs/internals.md#bluetooth-on-usb-power); not the
         * Q2's own OTG bus power or the 3 s after it ends, its VBUS dying away (usb_otg.c
         * usb_otg_own_vbus), nor a cable whose remembered choice is USB Audio, nor one Automatic
         * shows the iPhone on: a car that takes it plays over USB, and a host that does not gets the
         * connect then (usb_car_storage) */
        if (!usb_otg_own_vbus() && usb_remembered() != USB_AUDIO && usb_remembered() != USB_AUTO) headset_bt_usb();
        /* below, nothing is chosen for a cable that comes while stock's link status is not 0: said, as the
         * car's first cable of 2026-11-22 showed no iPhone and nothing said why */
        char line[64];
        if (g_usblink_status && tk_snprintf(line, sizeof line, "cable in with stock's link status %d: no choice made",
                                            g_usblink_status) > 0)
            usb_log(line, -1);
    }
    u.det_known = 1;
    if (det && !u.det && g_usblink_status == 0) {
        u.choice = USB_AUTO, u.chosen = 0;
        u.plug_at = (unsigned)time_now_ms();
        int pick = usb_remembered();
        if (pick == USB_DEBUG) /* Automatic's iPhone parked the ADB gadget on an earlier cable */
            system("/usr/bin/q2adb-gadget unpark >/dev/null 2>&1");
        if (pick == USB_FILES || pick == USB_AUDIO) {
            u.choice = pick; /* stock's own path, in the choice's mode for this cable */
            if ((g_usbmode == USB_MODE_DAC) != (pick == USB_AUDIO)) usb_mode(pick == USB_AUDIO);
        } else {
            g_usblink_status = USB_LINK_HELD;
            if (pick == USB_AUTO) {
                /* the iPhone first: a car keeps it, anything else gets Transfer Files (usb_car_storage) */
                usb_log("Automatic: the iPhone shown", -1);
                usb_car_start();
            } else if (pick == USB_ASK) {
                /* no medium: a probe; in a --dev build the ADB gadget already there is one */
                u.probe_up = !usb_adb_bound();
                if (u.probe_up) system("storage_config.sh start /dev/null >/dev/null 2>&1");
                u.probing = 1, u.probe_at = (unsigned)time_now_ms();
            } else {
                u.choice = pick;
                usb_charger_in();
            }
        }
    } else if (!det && u.det) {
        /* The probe's gadget goes with the cable, whether it still waited or its chooser was up
         * unanswered: left running, it kept configfs mounted and the ADB gadget parked, and the
         * next cable's probe could not start (docs/internals.md#usb-cable-events). */
        u.probing = 0, u.after_car = 0;
        usb_probe_stop();
        usb_car_stop();
        usb_page_close(&u.chooser);
        usb_page_close(&u.debug);
        if (u.mode_saved) g_usbmode = (unsigned char)(u.mode_saved - 1);
        u.mode_saved = 0, u.choice = USB_AUTO;
        usb_toast("usbtoast_unplugged", "Unplugged");
    }
    u.det = det;
    usb_probe_look();
    if (det && u.choice == USB_DEBUG && !u.debug && !u.chooser && usb_host() && !u.chosen) {
        u.chosen = 1;
        usb_debug_open(); /* remembered: once a computer is there */
    }
}

/* Automatic's host was not a car, and the iPhone gadget is gone (usb_car.c): Transfer Files for this cable,
 * stock's storage gadget and its mode page, as the picker's Transfer Files; and the Bluetooth connect a cable
 * in starts (headset_bt_usb), held back while the iPhone was shown. */
static void usb_car_storage(void) {
    usb_log("Automatic: not a car, Transfer Files", (int)((unsigned)time_now_ms() - u.plug_at));
    u.chosen = 1;
    usb_apply(USB_FILES, 0);
    if (!usb_otg_own_vbus()) headset_bt_usb();
}

/* ---- the ADB prompt (--dev builds) ---- */

#if Q2POD_DEV
/* adbd's question answered (adb_trust.c's card): "OK" lets the computer in, "NO" leaves it out. */
void usb_ui_auth_answer(const char *a) {
    if (u.fd > 0) send(u.fd, a, 2, 0);
}

/* adbd's UI socket, connected once a second until it is there, and its questions. */
static void auth_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    if (u.fd <= 0) {
        if (now - u.auth_at < 1000) return;
        u.auth_at = now;
        /* MIPS Linux numbers the socket types differently from x86 (MIPS ABI: SOCK_DGRAM 1, SOCK_STREAM 2), and adbd's
         * socket is a stream: 1 here would be a datagram socket and connect would never reach adbd's listener. */
        int fd = socket(1 /* AF_UNIX */, 2 /* SOCK_STREAM (MIPS) */, 0);
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
        u.fd = fd;
        return;
    }
    int n = (int)recv(u.fd, u.in, sizeof u.in - 1, 0);
    if (n == 0 || (n < 0 && *__errno_location() != 11 /* EAGAIN */)) {
        close(u.fd);
        u.fd = 0;
        adb_prompt_close(); /* adbd went: nothing to answer */
        return;
    }
    if (n < 3 || u.in[0] != 'P' || u.in[1] != 'K') return;
    u.in[n] = 0;
    tk_snprintf(u.key, sizeof u.key, "%s", u.in + 2);
    /* the card (adb_trust.c), over whatever shows; another card up answers NO, and the computer
     * asks again */
    if (!adb_prompt_open(u.key)) usb_ui_auth_answer("NO");
}
#else
static void auth_poll(void) {}
#endif

/* ringnav_sleep: the cable's edges, the probe's answer, charging's poll, a toast USB or charging left,
 * and adbd's questions. An edge runs stock's cable read (get_battery_capacity, which sets
 * g_usbdet_value as its one-second timer would) and its USB check (check_usb_status, hooked as
 * ringnav_usb_status, which runs usb_ui_status first) now, in this pass. */
void usb_ui_poll(void) {
    usb_event_start(); /* once */
    unsigned long long edge;
    if (usb_event_take(&edge)) {
        u.by_event = 1, u.edge_ns = edge;
        get_battery_capacity();
        ringnav_usb_status();
        u.by_event = 0;
    }
    if (u.probing && (unsigned)time_now_ms() - u.probe_look_at >= USB_PROBE_LOOK_MS) usb_probe_look();
    int car = usb_car_poll();
    if (u.after_car && !usb_car_busy()) {
        int pick = u.after_car - 1;
        u.after_car = 0;
        if (usb_ui_cable()) usb_switch_to(USB_AUTO, pick, 2);
    }
    if (car == USB_CAR_STORAGE && g_usbdet_value == 1) usb_car_storage();
    else if (car == USB_CAR_CHARGER) usb_charger_in();
    usb_record_poll();  /* usb_record.c: a cable edge's second line */
    usb_otg_poll();     /* usb_otg.c: a Play asked while USB audio connected */
    usb_keys_poll();    /* usb_keys.c: a USB-C headset's buttons */
    charging_poll();    /* charging.c's plug-in screen and weak charger */
    toast_later_poll(); /* a toast USB or charging left */
    auth_poll();
}
