/* Save Diagnostics (--dev builds only, docs/internals.md#save-diagnostics): a row heading
 * Settings > General > About whose Centre writes what a device problem is read from to one file on
 * the SD card, DIAG_FILE, so it can be read from the card on a computer without a shell: the build,
 * the uptime, the USB controller's state and the gadget bound to it (configfs: each gadget's UDC,
 * functions and a storage function's LUN file), ps, dmesg's tail and the tail of demo's log, which
 * q2player, started by demo, writes to as well. Nothing personal: no config.ini, no Wi-Fi
 * settings, no keys and no listing of the card. A release build has no row (Q2POD_DEV 0 leaves
 * diag_about_row empty and the rest unreferenced). One shell command writes it beside the target
 * and moves it into place, so an interrupted save leaves the last whole file. */
#include "navigation.h"

#define DIAG_NAME "q2-diagnostics.txt"
#define DIAG_FILE "/mnt/mmc/" DIAG_NAME
#define DIAG_ROW "_diag_row" /* settings_pages.c about_rows leaves this row its own click */
#define DIAG_NO_CARD 2       /* the script's exit when the card is not mounted */

static const char diag_script[] =
    "grep -q ' /mnt/mmc ' /proc/mounts || exit 2; f=" DIAG_FILE "; {"
    " echo '== version'; echo '" Q2POD_VERSION "';"
    " echo '== date'; date; echo '== uptime'; cat /proc/uptime;"
    " echo '== udc'; for s in /sys/class/udc/*/state;"
    " do [ -f \"$s\" ] && echo \"$s: $(cat $s)\"; done;"
    " echo '== gadgets'; for g in /sys/kernel/config/usb_gadget/*; do [ -d \"$g\" ] || continue;"
    " echo \"$g UDC=$(cat $g/UDC)\"; ls $g/functions; ls -l $g/configs/*/ | grep -- '->';"
    " for l in $g/functions/*/lun.*/file; do [ -f \"$l\" ] && echo \"$l: $(cat $l)\"; done; done;"
    " echo '== mounts'; grep -E 'configfs|functionfs|mmc' /proc/mounts;"
    " echo '== battery (CW2015, used; AXP2101, unused)';"
    " for b in /sys/devices/i2c-0/0-0062/cw2015_capacity /sys/devices/i2c-0/0-0062/cw2015_voltage"
    " /sys/class/power_supply/battery/capacity /sys/class/power_supply/battery/voltage_now;"
    " do echo \"$b: $(cat $b)\"; done;"
    " echo '== ps'; ps; echo '== dmesg'; dmesg | tail -n 300;"
    " echo '== q2-demo.err'; tail -n 100 /tmp/q2-demo.err;"
    " echo '== q2-demo.log (demo and q2player)'; tail -n 300 /tmp/q2-demo.log;"
    " } > $f.tmp 2>&1 && mv $f.tmp $f && sync";

/* The row's Centre or tap: the file written, and a toast saying where (or why not). */
static int diag_save(void *ctx, void *event) {
    int status = system(diag_script);
    int code = status >= 0 && !(status & 0x7f) ? (status >> 8) & 0xff : -1;
    toast(!code                  ? "Saved to SD card: " DIAG_NAME
          : code == DIAG_NO_CARD ? "No SD card"
                                 : "Couldn't save diagnostics");
    return 0;
}

/* ringnav_about, after stock's rows and CFW. Version: in a --dev build the Save Diagnostics row,
 * above them. */
void diag_about_row(void *view) {
    if (!Q2POD_DEV || !view) return;
    void *label = list_row(view, (const char *)0, diag_save, (void *)0);
    void *item = P(P(label, W_PARENT), W_PARENT);
    widget_set_text_utf8(label, "Save Diagnostics");
    widget_set_prop_int(item, DIAG_ROW, 1);
    widget_restack(item, 0); /* first: the page's one selectable row is in sight as it opens */
}

/* about_rows: the Save Diagnostics row, which takes the selection as a row does. */
int diag_row(void *item) { return widget_get_prop_int(item, DIAG_ROW, 0); }
