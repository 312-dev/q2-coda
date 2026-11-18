/* The USB record (docs/internals.md#usb-record): a line on the card for each cable edge, the same
 * pins USB_RECORD_LATER_MS after it, and each change of OTG host power, so ordinary use shows
 * whether the Q2 ever powers the bus with a computer on it, and whether a plug enumerated.
 * tools/q2usb.py reads it and flags both. The lines are held in RAM while the card is away
 * (card_store.c's log).
 */
#include "card_store.h"
#include "navigation.h"

#define USB_RECORD_FILE CARD_STORE_DIR "/q2-usb.log"
#define USB_RECORD_OLD CARD_STORE_DIR "/q2-usb.1.log"
#define USB_RECORD_MAX 131072 /* moved to .1 at this size: some 700 lines */
#define USB_RECORD_RAM 4096   /* the RAM ring while the card is away */
#define USB_RECORD_LATER_MS 5000
/* the vendor dwc2's read-only id_status, dwc2_mode and connected */
#define USB_OTG "/sys/bus/platform/devices/13500000.otg/"
#define USB_HOST_DEV "/sys/bus/usb/devices/1-1/idVendor" /* a device on the Q2's host port */
#define USB_ONLINE "/sys/class/power_supply/usb/online"
#define USB_RECORD_UDC "/sys/class/udc/13500000.otg/state"

static struct {
    card_log log;
    unsigned later_at;
    int later; /* 1: a plug's second line is due, 2: an unplug's */
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

void usb_record(const char *what) {
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
                    "%lld %u %s PD18=%d PD17=%d PD19=%d PC20=%d PE22=%d otg=%d id=%s mode=%s "
                    "conn=%s vbus=%s udc=%s hostdev=%s\n",
                    wall_time(), (unsigned)time_now_ms(), what, toolsGpioGetValue("PD18"),
                    toolsGpioGetValue("PD17"), toolsGpioGetValue("PD19"), toolsGpioGetValue("PC20"),
                    toolsGpioGetValue("PE22"), g_last_otgid, id, mode, conn, online, udc, dev);
    if (n <= 0 || n >= (int)sizeof line) return;
    rotate();
    card_log_append(card_log_at(&rec.log, USB_RECORD_FILE, USB_RECORD_RAM), line, (unsigned)n);
}

/* A cable edge (usb_ui_status): its line now, and a second one USB_RECORD_LATER_MS on. */
void usb_record_edge(int in) {
    usb_record(in ? "in" : "out");
    rec.later = in ? 1 : 2;
    rec.later_at = (unsigned)time_now_ms();
}

/* From usb_ui_poll, every UI pass. */
void usb_record_poll(void) {
    if (rec.later && (unsigned)time_now_ms() - rec.later_at >= USB_RECORD_LATER_MS) {
        usb_record(rec.later == 1 ? "in+5s" : "out+5s");
        rec.later = 0;
    }
}
