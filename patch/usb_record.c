/* The USB record (docs/internals.md#usb-record): a line on the card for each cable edge, the same
 * pins USB_RECORD_LATER_MS after it, each change of OTG host power and each Standby and wake, so
 * ordinary use shows whether the Q2 ever powers the bus with a computer on it, and whether a plug
 * enumerated. tools/q2usb.py reads it and flags both. The lines are held in RAM while the card is
 * away (card_store.c's log).
 */
#include "card_store.h"
#include "navigation.h"

#define USB_RECORD_FILE CARD_STORE_DIR "/q2-usb.log"
#define USB_RECORD_OLD CARD_STORE_DIR "/q2-usb.1.log"
#define USB_RECORD_MAX 131072 /* moved to .1 at this size: some 700 lines */
#define USB_RECORD_RAM 4096   /* the RAM ring while the card is away */
#define USB_RECORD_LATER_MS 5000
/* PD18's pull-up as S12usbcharge sets it, and its check: get_func answers "input pull_up" */
#define USB_PULL_UP "/usr/bin/cmd_gpio set_func PD18 input pull_up >/dev/null 2>&1"
#define USB_PULL_FUNC "/tmp/q2-pd18.func"
#define USB_PULL_CHECK "/usr/bin/cmd_gpio get_func PD18 >" USB_PULL_FUNC " 2>&1"
#define USB_FLUSH_MS 5000 /* lines held with the card away are tried on the card this often */
/* the vendor dwc2's read-only id_status, dwc2_mode and connected */
#define USB_OTG "/sys/bus/platform/devices/13500000.otg/"
#define USB_HOST_DEV "/sys/bus/usb/devices/1-1/idVendor" /* a device on the Q2's host port */
#define USB_ONLINE "/sys/class/power_supply/usb/online"
#define USB_RECORD_UDC "/sys/class/udc/13500000.otg/state"

static struct {
    card_log log;
    unsigned later_at;
    int later; /* the second line due: 1 a plug's, 2 an unplug's, 3 a wake's */
    unsigned flush_at;
} rec __attribute__((section(".scratch")));

/* path's first line, its newline cut and spaces as _, into out; "?" when it can't be read */
static void first_line(const char *path, char *out, int cap) {
    void *f = fopen(path, "rb");
    int n = f ? (int)fread(out, 1, cap - 1, f) : 0;
    if (f) fclose(f);
    if (n <= 0) out[0] = '?', n = 1;
    out[n] = 0;
    for (char *p = out; *p; ++p)
        if (*p == '\n')
            *p = 0;
        else if (*p == ' ')
            *p = '_';
}

/* The file moved to .1 once it has reached USB_RECORD_MAX, so the two hold the last 1400 or so. */
static void rotate(void) {
    if (!card_mounted()) return;
    void *f = fopen(USB_RECORD_FILE, "rb");
    if (!f) return;
    long size = fseek(f, 0, 2 /* SEEK_END */) ? 0 : ftell(f);
    fclose(f);
    if (size >= USB_RECORD_MAX) rename(USB_RECORD_FILE, USB_RECORD_OLD);
}

/* A line: what, then the pins and the controller, then PD18's pull when pull is not 0. */
static void record(const char *what, const char *pull) {
    char id[16], mode[16], conn[8], online[8], udc[24], dev[8], line[240];
    first_line(USB_OTG "id_status", id, sizeof id);
    first_line(USB_OTG "dwc2_mode", mode, sizeof mode);
    first_line(USB_OTG "connected", conn, sizeof conn);
    first_line(USB_ONLINE, online, sizeof online);
    first_line(USB_RECORD_UDC, udc, sizeof udc);
    first_line(USB_HOST_DEV, dev, sizeof dev); /* "?": nothing enumerated on the host port */
    /* PD19, a GPIO input with a pull-up beside the USB pins that stock never reads, is recorded in
     * case it is the Type-C chip's (docs/internals.md#otg-host-power) */
    int n =
        tk_snprintf(line, sizeof line,
                    "%u %u %s PD18=%d PD17=%d PD19=%d PC20=%d PE22=%d otg=%d id=%s mode=%s "
                    "conn=%s vbus=%s udc=%s hostdev=%s%s%s\n",
                    (unsigned)wall_time(), (unsigned)time_now_ms(), what, toolsGpioGetValue("PD18"),
                    toolsGpioGetValue("PD17"), toolsGpioGetValue("PD19"), toolsGpioGetValue("PC20"),
                    toolsGpioGetValue("PE22"), g_last_otgid, id, mode, conn, online, udc, dev,
                    pull ? " pull=" : "", pull ? pull : "");
    if (n <= 0 || n >= (int)sizeof line) return;
    rotate();
    card_log_append(card_log_at(&rec.log, USB_RECORD_FILE, USB_RECORD_RAM), line, (unsigned)n);
    /* and in the unified log (ulog.c, docs/internals.md#unified-log), from the event word on: the
     * line there has its own time and uptime */
    const char *ev = line;
    for (int sp = 0; *ev && sp < 2; ++ev) sp += *ev == ' ';
    ulog('I', "usb", ev);
}

void usb_record(const char *what) { record(what, 0); }

/* A short line of the pins alone, for usb_otg.c's trace while the ID or VBUS is moving
 * (usb_otg_poll): on the card only, not in the unified log, 4 a second at most. otgst is stock's
 * g_otg_status (2: its host pass has the USB DAC). */
void usb_record_pins(int hostdev) {
    char line[112];
    int n = tk_snprintf(line, sizeof line, "%u %u pins PD18=%d PD17=%d PC20=%d PE22=%d otg=%d hostdev=%d otgst=%d\n",
                        (unsigned)wall_time(), (unsigned)time_now_ms(), toolsGpioGetValue("PD18"),
                        toolsGpioGetValue("PD17"), toolsGpioGetValue("PC20"), toolsGpioGetValue("PE22"),
                        g_last_otgid, hostdev, g_otg_status);
    if (n <= 0 || n >= (int)sizeof line) return;
    rotate();
    card_log_append(card_log_at(&rec.log, USB_RECORD_FILE, USB_RECORD_RAM), line, (unsigned)n);
}

/* PD18's pull as soc_gpio reports it, in a line; one that is not the pull-up is set again, and the
 * line's word says it was off. */
static void pull_check(const char *what, const char *off) {
    char func[32];
    system(USB_PULL_CHECK);
    first_line(USB_PULL_FUNC, func, sizeof func);
    int up = strstr(func, "pull_up") != 0;
    if (!up) system(USB_PULL_UP);
    record(up ? what : off, func);
}

/* platform_init's hook: stock sets the USB pins up there (PD18 input pull_up, tools/build.py
 * ID_PULL_UP), so the line after it is the record's first of a run, and its pull says whether the
 * pull-up held. */
int ringnav_platform_init(void) {
    int result = stock_platform_init_trampoline();
    otg_hold(); /* usb_otg.c: the PHY's ID pull-up, the controller held at device */
    pull_check("boot", "boot-pull-off");
    return result;
}

/* A cable edge (usb_ui_status): its line now, and a second one USB_RECORD_LATER_MS on. */
void usb_record_edge(int in) {
    usb_record(in ? "in" : "out");
    rec.later = in ? 1 : 2;
    rec.later_at = (unsigned)time_now_ms();
}

/* Back from Standby (system.c ringnav_standby, as toolsIntoStandby returns). The GPIO driver's
 * sleep setting for PD18 is a pull-up too (the device tree's gpio-sleep-pullup), and a wake kept
 * the pull-up on 2026-11-17, but what it restores is the driver's, so the pull-up is set again;
 * then the pins now and USB_RECORD_LATER_MS on, with the pull checked, where a cable's link
 * dropping at a wake shows. */
void usb_record_wake(void) {
    system(USB_PULL_UP);
    otg_hold(); /* the PHY's init at the resume writes USBPCR again (usb_otg.c) */
    usb_record("wake");
    rec.later = 3;
    rec.later_at = (unsigned)time_now_ms();
}

/* From usb_ui_poll, every UI pass. */
void usb_record_poll(void) {
    if (rec.later && (unsigned)time_now_ms() - rec.later_at >= USB_RECORD_LATER_MS) {
        if (rec.later == 3)
            pull_check("wake+5s", "wake+5s-pull-off");
        else
            usb_record(rec.later == 1 ? "in+5s" : "out+5s");
        rec.later = 0;
    }
    /* Lines held while the card was away (Standby unmounts it) reach it without waiting for the
     * next event. */
    unsigned now = (unsigned)time_now_ms();
    if (rec.log.len && now - rec.flush_at >= USB_FLUSH_MS) {
        rec.flush_at = now;
        card_log_flush(&rec.log);
    }
}
