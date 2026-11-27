/* q2sysd: Vamp OS's system service (docs/vamp-os.md#who-owns-what, docs/sysd.md), its first slice: the power key,
 * the screen's light, and the battery.
 *
 *   - The power key (KEY_POWER on any input device: md-gpio-keys' power button, the PMIC's key, and the touchscreen's
 *     double tap, which hyn_touch reports as KEY_POWER while its gesture mode is on): a press shorter than HOLD_MS
 *     turns the screen off or on; held HOLD_MS it powers the Q2 off (POWEROFF, init's shutdown: the services stopped,
 *     the file systems unmounted).
 *   - The screen: off is the backlight's bl_power 4 (FB_BLANK_POWERDOWN) and the touchscreen's gesture mode on, as
 *     stock's screen_action does (docs/internals.md#screen-off-and-wake); on is bl_power 0 and gestures off. q2ui
 *     reads the same bl_power and drops input while it is dark (ui/ui.c dark). Lit, it goes off once nothing has
 *     used the Q2 for stock's Screen Off Time ([SYSSET] LIGHTTIME, docs/sysd.md#the-screen-off-time): any input
 *     device's key, touch or wheel, or Q2SYS/1 awake.
 *   - The battery: the CW2015's level and voltage, the charger's state, read every BATTERY_S and at each change of
 *     the screen, written to /run/vamp/battery and told to watchers.
 *
 *   - Charge Limit (Q2POD CHARGELIMIT in config.ini, as the payload's charge_poll): charging stopped at 80% and handed
 *     back at 75% through the charger's active-low enable line, PE22.
 *   - USB modes: adb (q2adb's gadget, the default), storage (the card to the computer: the player stopped, the card
 *     unmounted, a mass-storage gadget as stock's storage_config.sh makes it) and dac (the Q2 a USB Audio 2.0 DAC for
 *     the computer: the player stopped, a UAC2 gadget as stock's uac_device_config.sh makes it, and the q2dacloop
 *     service up to carry its audio to the DAC, docs/dacloop.md) and car (Car Audio: today's Vamp's q2car shows the
 *     host an iPhone and q2-iap decides whether it is a car, docs/car-audio-os.md; anything else is back to adb), and
 *     back.
 *   - The cable (docs/sysd.md#the-cable): ac/online read every CABLE_MS; a cable in starts the mode the USB setting
 *     ([Q2POD] USBPICK, the payload's, patch/usb_ui.c) names, Automatic's iPhone ending in storage for a computer,
 *     and a cable out is adb again.
 *   - Standby: the screen off, the music paused, Wi-Fi off, then the kernel's suspend (/sys/power/state mem) until a
 *     wake source (the power key) resumes it; then the screen on. Stock's Auto Standby and Auto Power Off
 *     ([SYSSET] STANDBYMODE, SAVEPOWERMODE, docs/sysd.md#auto-standby-and-auto-power-off): Standby, or the power off,
 *     once the Q2 is unused that long, counted as stock's check_to_poweroption counts (music, Car Audio and USB
 *     storage hold both, any cable holds Standby); Auto Power Off with a cable in waits on the charge first.
 *   - The jacks (docs/sysd.md#the-jacks): PA07 and PA08 read each second, an unplug counted once it lasts and pausing
 *     music that plays.
 *   - Heat (docs/sysd.md#heat): today's Vamp's overheat guardian, its core and PMIC reader linked in from patch/, its
 *     warnings told and shown through Q2UI/1, its power-off done here; not on a card boot, where today's q2guard runs.
 *   - A low battery (docs/sysd.md#a-low-battery): stock's warning under 16% and its power off after five readings at
 *     3400 mV or 0% with no cable.
 *   - The watchdog (docs/sysd.md#the-watchdog): /dev/jz_watchdog started at 60 s and fed each second, stopped across
 *     Standby and at a clean end.
 *
 * Q2SYS/1 on /run/vamp/sys.sock (docs/platform/system.md): screen on|off|toggle, brightness P, battery, usb, usb adb|storage|dac|car,
 * standby, heat, heat cancel, jack, watch, caps.
 * --root prefixes every path, --poweroff and --reboot name the commands, --heat-file F gives the temperature in place of the PMIC and --time-scale N runs Auto Standby's and Auto Power Off's
 * times N times faster, for test/sysd.py. */
#define _GNU_SOURCE
#include <dirent.h>
#include <pthread.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "q2perm.h" /* perm/: the permission check */
/* patch/: the overheat guardian's decisions and the PMIC's reader (Q2GUARD_HOST). Its header also declares q2guard.c's
 * own helpers, which q2sysd has its own of: renamed out of the way, as the core uses none of them. */
#define now_ms q2guard_now_ms
#define read_int q2guard_read_int
#define slurp q2guard_slurp
#include "q2guard.h"
#undef now_ms
#undef read_int
#undef slurp

#define HOLD_MS 3000    /* the power key held this long powers off */
#define BATTERY_S 30    /* the battery read this often */
#define INPUTS 8        /* input devices watched */
#define CLIENTS 8       /* Q2SYS/1 connections */
#define RESCAN_MS 10000 /* input devices looked for again this often while none has the power key */
#define CHARGE_STOP 80   /* Charge Limit's stop and resume levels (the payload's CHARGE_STOP, CHARGE_RESUME) */
#define CHARGE_RESUME 75
#define PE22 150         /* the charger's active-low enable: port E (gpiochip4, base 128) line 22 */
#define CABLE_MS 1000    /* ac/online read this often: the cable's in and out (no event reaches userspace, docs/usb.md);
                          * config.ini's Screen Off Time and the idle time looked at as often */

static char root[256], poweroff_cmd[256] = "/sbin/poweroff", reboot_cmd[256] = "/sbin/reboot";

static char *path(const char *rel) {
    static char b[4][400];
    static int i;
    i = (i + 1) & 3;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}

static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2sysd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

static int read_int(const char *p, int *v) {
    char b[32];
    FILE *f = fopen(p, "r");
    int ok = f && fgets(b, sizeof b, f);
    if (f) fclose(f);
    if (ok) *v = atoi(b);
    return ok ? 0 : -1;
}

static void read_word(const char *p, char *out, size_t room) {
    FILE *f = fopen(p, "r");
    out[0] = 0;
    if (f && fgets(out, (int)room, f)) out[strcspn(out, "\r\n")] = 0;
    if (f) fclose(f);
}

static int write_text(const char *p, const char *text) {
    int fd = open(p, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    int ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text);
    close(fd);
    return ok ? 0 : -1;
}

/* ---- the screen ---- */

static char backlight[300]; /* the first /sys/class/backlight device, "" with none */
static int screen_on = 1;
static unsigned long long used_at; /* the last use: an input event, Q2SYS/1 awake, the screen lit, the setting changed */
static int power_fresh = 1; /* a use or a cable change since the last tick: Auto Standby's and Auto Power Off's times
                             * whole again, as stock's reset_poweroptions_timer(1, 1, 1) */

static void use_now(void) { used_at = now_ms(), power_fresh = 1; }

/* heat's (below): the guardian's state, its Cancel asked, a suspend since its last step, whether it watches */
static struct guard guard;
static int heat_cancel, heat_slept, heat_on;
static int jack_in[2]; /* the jacks' (below) */

static void find_backlight(void) {
    DIR *d = opendir(path("/sys/class/backlight"));
    backlight[0] = 0;
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.' && !backlight[0]) snprintf(backlight, sizeof backlight, "%s/%.60s", path("/sys/class/backlight"), e->d_name);
    if (d) closedir(d);
    int v;
    char p[340];
    snprintf(p, sizeof p, "%s/bl_power", backlight);
    if (backlight[0] && !read_int(p, &v)) screen_on = v == 0;
}

static void tell(const char *fmt, ...);
static void charge_poll(void);

static int set_screen(int on) {
    char p[340];
    if (!backlight[0]) return -1;
    snprintf(p, sizeof p, "%s/bl_power", backlight);
    if (write_text(p, on ? "0" : "4")) return say("cannot write %s", p), -1;
    /* the touchscreen's gestures (a double tap as KEY_POWER) while dark, as stock's double_onclick_wake */
    write_text(path("/sys/hynitron_debug/hyngescontrol"), on ? "0" : "1");
    screen_on = on;
    if (on) use_now();
    say("screen %s", on ? "on" : "off");
    tell("screen %s", on ? "on" : "off");
    return 0;
}

/* brightness 0 to 100 over the device's max_brightness, linearly (stock's own scale is not read yet: docs/sysd.md) */
static int set_brightness(int pct) {
    char p[340], v[16];
    int max;
    if (!backlight[0] || pct < 0 || pct > 100) return -1;
    snprintf(p, sizeof p, "%s/max_brightness", backlight);
    if (read_int(p, &max) || max <= 0) return -1;
    snprintf(v, sizeof v, "%d", (pct * max + 50) / 100);
    snprintf(p, sizeof p, "%s/brightness", backlight);
    return write_text(p, v);
}

/* ---- the battery ---- */

struct battery { int level, mv, cable; char status[24]; };
static struct battery bat = {-1, -1, -1, ""};

/* The charger's status is the kernel's text ("Not charging" has a space): on Q2SYS/1 percent-encoded as Q2PLAY/1's
 * free-text fields are (docs/platform/README.md#the-common-shape), every byte below 0x21, '%', '"', '=' and 0x7f as %XX; in the file, one
 * key=value a line, as it is. */
static void battery_text(char *out, size_t room, const char *sep, int encode) {
    static const char hex[] = "0123456789ABCDEF";
    char st[80];
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)(bat.status[0] ? bat.status : "unknown"); *p && j + 4 < sizeof st; ++p)
        if (encode && (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f))
            st[j++] = '%', st[j++] = hex[*p >> 4], st[j++] = hex[*p & 15];
        else st[j++] = (char)*p;
    st[j] = 0;
    snprintf(out, room, "level=%d%smv=%d%sstatus=%s%scable=%d", bat.level, sep, bat.mv, sep, st, sep, bat.cable);
}

static void read_battery(void) {
    int v;
    struct battery was = bat;
    bat.level = read_int(path("/sys/devices/i2c-0/0-0062/cw2015_capacity"), &v) ? -1 : v;
    bat.mv = read_int(path("/sys/devices/i2c-0/0-0062/cw2015_voltage"), &v) ? -1 : v;
    read_word(path("/sys/class/power_supply/battery/status"), bat.status, sizeof bat.status);
    bat.cable = read_int(path("/sys/class/power_supply/ac/online"), &v) ? -1 : v;
    if (memcmp(&was, &bat, sizeof bat)) {
        char text[160], tmp[420];
        battery_text(text, sizeof text, "\n", 0);
        snprintf(tmp, sizeof tmp, "%s.q2sysd", path("/run/vamp/battery"));
        FILE *f = fopen(tmp, "w");
        if (f) {
            fprintf(f, "%s\n", text);
            if (!fclose(f)) rename(tmp, path("/run/vamp/battery"));
        }
        battery_text(text, sizeof text, " ", 1);
        tell("battery %s", text);
    }
    charge_poll();
}

/* ---- a line to another service's socket (Q2PLAY/1, Q2NET/1): sent, its answer read; -1 when it is not there ---- */

static int ask(const char *sock, const char *hello, const char *line) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    const char *p = path(sock);
    if (strlen(p) >= sizeof a.sun_path) return -1;
    memcpy(a.sun_path, p, strlen(p) + 1);
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a)) return fd >= 0 ? close(fd), -1 : -1;
    char out[256], in[256];
    int n = snprintf(out, sizeof out, "%s\n%s\n", hello, line), ok = write(fd, out, (size_t)n) == n;
    struct pollfd pf = {.fd = fd, .events = POLLIN};
    size_t got = 0;
    while (ok && got < sizeof in - 1 && poll(&pf, 1, 2000) > 0) { /* the greeting and the answer: two lines */
        ssize_t r = read(fd, in + got, sizeof in - 1 - got);
        if (r <= 0) break;
        got += (size_t)r;
        in[got] = 0;
        char *nl = strchr(in, '\n');
        if (nl && strchr(nl + 1, '\n')) break;
    }
    close(fd);
    return ok ? 0 : -1;
}

/* a command of the system's, its path under --root (the tests' stand-ins) */
static int run_tool(const char *fmt, ...) {
    char cmd[900], rel[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(rel, sizeof rel, fmt, ap);
    va_end(ap);
    snprintf(cmd, sizeof cmd, "%s%s", root, rel);
    int r = system(cmd);
    return WIFEXITED(r) ? WEXITSTATUS(r) : -1;
}

/* ---- Charge Limit ---- */

static int charge_limit, charge_held; /* held: charging stopped by the limit */

static int config_int(const char *section, const char *key, int fallback) {
    char line[256], head[64];
    int in = 0, v = fallback;
    snprintf(head, sizeof head, "[%s]", section);
    FILE *f = fopen(path("/mnt/data/config.ini"), "r");
    size_t k = strlen(key);
    while (f && fgets(line, sizeof line, f)) {
        if (line[0] == '[') in = !strncmp(line, head, strlen(head));
        else if (in && !strncmp(line, key, k) && line[k] == '=') v = atoi(line + k + 1);
    }
    if (f) fclose(f);
    return v;
}

/* ---- the screen off time (docs/sysd.md#the-screen-off-time) ----
 * Stock's Screen Off Time, [SYSSET] LIGHTTIME: its six choices are setlight_time's table in demo, 15 s, 30 s, 1, 2, 5
 * and 30 minutes; any other value gives 0 there, never off. Stock's file says 1; a file without the key reads as
 * that. Read again when config.ini changes, and a change starts the time again, as config_lighttime does. */
static const int LIGHT_S[] = {15, 30, 60, 120, 300, 1800};
static int light_s = 30;
static long long light_stamp = -1;
/* Auto Standby (STANDBYMODE 1: setstandby_time's 300 s, else off) and Auto Power Off (SAVEPOWERMODE 1 to 4:
 * setsavepower_time's table, 1, 3, 5 and 10 minutes, else off), read with LIGHTTIME */
static const int SAVEPOWER_S[] = {60, 180, 300, 600};
static int standby_s, savepower_s, time_scale = 1;

static void light_poll(void) {
    struct stat st;
    long long stamp = stat(path("/mnt/data/config.ini"), &st) ? 0 : (long long)st.st_mtime * 1000003 + (long long)st.st_size;
    if (stamp == light_stamp) return;
    light_stamp = stamp;
    int v = config_int("SYSSET", "LIGHTTIME", 1);
    light_s = v >= 0 && v < (int)(sizeof LIGHT_S / sizeof *LIGHT_S) ? LIGHT_S[v] : 0;
    standby_s = config_int("SYSSET", "STANDBYMODE", 0) == 1 ? 300 : 0;
    v = config_int("SYSSET", "SAVEPOWERMODE", 0);
    savepower_s = v >= 1 && v <= 4 ? SAVEPOWER_S[v - 1] : 0;
    use_now();
    say("screen off time %d s, auto standby %d s, auto power off %d s (0: never)", light_s, standby_s, savepower_s);
}

/* the charger on (allow) or held off, through PE22 (active low: 1 holds it off) as the kernel's GPIO class gives it */
static int charger(int allow) {
    char p[300], n[16];
    snprintf(n, sizeof n, "%d", PE22);
    snprintf(p, sizeof p, "%s/gpio%d/value", path("/sys/class/gpio"), PE22);
    if (access(p, F_OK)) {
        write_text(path("/sys/class/gpio/export"), n);
        snprintf(p, sizeof p, "%s/gpio%d/direction", path("/sys/class/gpio"), PE22);
        write_text(p, "out");
        snprintf(p, sizeof p, "%s/gpio%d/value", path("/sys/class/gpio"), PE22);
    }
    if (write_text(p, allow ? "0" : "1")) return say("cannot write %s", p), -1;
    say("charging %s", allow ? "allowed" : "held at the limit");
    return 0;
}

static void charge_poll(void) {
    if (!charge_limit) {
        if (charge_held && !charger(1)) charge_held = 0;
        return;
    }
    if (bat.level < 0) return;
    if (!charge_held && bat.level >= CHARGE_STOP && !charger(0)) charge_held = 1, tell("charge held level=%d", bat.level);
    else if (charge_held && bat.level <= CHARGE_RESUME && !charger(1)) charge_held = 0, tell("charge allowed level=%d", bat.level);
}

/* ---- USB modes ---- */

enum { USB_ADB, USB_STORAGE, USB_DAC, USB_CAR };
static int usb_mode;      /* USB_ADB, or the card the computer's, the Q2 its DAC, or an iPhone for a car */
static const char *const USB_NAMES[] = {"adb", "storage", "dac", "car"};
static int car_told;      /* the car's verdict told to watchers */
static int car_auto;      /* the iPhone shown for Automatic at a cable: a computer then gets the card, not adb */
static char card_dev[64]; /* the card's block device while it is shared, as /proc/mounts named it */

static int find_card(char *dev, size_t room) {
    char line[600];
    FILE *f = fopen(path("/proc/mounts"), "r");
    dev[0] = 0;
    while (f && fgets(line, sizeof line, f))
        if (strstr(line, " /mnt/mmc ")) snprintf(dev, room, "%.*s", (int)strcspn(line, " "), line);
    if (f) fclose(f);
    return dev[0] ? 0 : -1;
}

/* a gadget's attribute: configfs makes them with each directory; opened O_CREAT too, so a test's plain directory takes it */
static int gadget_write(const char *p, const char *text) {
    int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    int ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text);
    close(fd);
    return ok ? 0 : -1;
}

static int bind_udc(const char *g);

/* stock's storage_config.sh's gadget: mass storage of one LUN, the card's block device, writable and removable */
static int storage_gadget(const char *dev) {
    char g[300], p[400];
    snprintf(g, sizeof g, "%s/usb_gadget/q2storage", path("/sys/kernel/config"));
    if (mkdir(g, 0755) && errno != EEXIST) return -1;
    static const char *const files[][2] = {{"idVendor", "0x0525"}, {"idProduct", "0xa4a5"}, {"bcdUSB", "0x0200"},
                                          {"bcdDevice", "0x0100"}, {"bDeviceClass", "0x00"}};
    for (unsigned i = 0; i < sizeof files / sizeof *files; ++i) snprintf(p, sizeof p, "%s/%s", g, files[i][0]), gadget_write(p, files[i][1]);
    /* configfs makes these three with the gadget; made here too, for a test's plain directory (EEXIST otherwise) */
    snprintf(p, sizeof p, "%s/strings", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/configs", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/functions", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/strings/0x409", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/strings/0x409/product", g), gadget_write(p, "Vamp");
    snprintf(p, sizeof p, "%s/configs/c.1", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/configs/c.1/MaxPower", g), gadget_write(p, "20");
    snprintf(p, sizeof p, "%s/functions/mass_storage.msg", g), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/functions/mass_storage.msg/lun.0", g), mkdir(p, 0755);
    static const char *const lun[][2] = {{"ro", "0"}, {"removable", "1"}, {"nofua", "0"}, {"cdrom", "0"}};
    for (unsigned i = 0; i < sizeof lun / sizeof *lun; ++i)
        snprintf(p, sizeof p, "%s/functions/mass_storage.msg/lun.0/%s", g, lun[i][0]), gadget_write(p, lun[i][1]);
    snprintf(p, sizeof p, "%s/functions/mass_storage.msg/lun.0/file", g);
    if (gadget_write(p, dev)) return -1;
    char link[400], target[400];
    snprintf(link, sizeof link, "%s/configs/c.1/mass_storage.msg", g);
    snprintf(target, sizeof target, "%s/functions/mass_storage.msg", g);
    if (symlink(target, link) && errno != EEXIST) return -1;
    return bind_udc(g);
}

static void storage_gadget_remove(void) {
    char g[300], p[400];
    snprintf(g, sizeof g, "%s/usb_gadget/q2storage", path("/sys/kernel/config"));
    snprintf(p, sizeof p, "%s/UDC", g), gadget_write(p, "\n");
    snprintf(p, sizeof p, "%s/configs/c.1/mass_storage.msg", g), unlink(p);
    static const char *const dirs[] = {"functions/mass_storage.msg/lun.0", "functions/mass_storage.msg", "configs/c.1",
                                       "strings/0x409", ""};
    for (unsigned i = 0; i < sizeof dirs / sizeof *dirs; ++i) snprintf(p, sizeof p, "%s/%s", g, dirs[i]), rmdir(p);
}

static int bind_udc(const char *g) {
    char udc[256] = "", p[400];
    DIR *d = opendir(path("/sys/class/udc"));
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.') snprintf(udc, sizeof udc, "%s", e->d_name);
    if (d) closedir(d);
    snprintf(p, sizeof p, "%s/UDC", g);
    return *udc && !gadget_write(p, udc) ? 0 : -1;
}

/* USB DAC mode's gadget, with stock's uac_device_config.sh's descriptors, so a computer that knew the Q2 as a DAC
 * knows it again: one UAC2 function, capture only (the computer plays into it), stereo, 32-bit, the rates stock
 * offers, asynchronous, no mute or volume control (the Q2's own volume stays the DAC's) */
static int dac_gadget(void) {
    char g[300], p[400];
    snprintf(g, sizeof g, "%s/usb_gadget/q2dac", path("/sys/kernel/config"));
    if (mkdir(g, 0755) && errno != EEXIST) return -1;
    static const char *const top[][2] = {{"idVendor", "0x20B1"}, {"idProduct", "0x301F"}, {"bcdUSB", "0x200"},
                                        {"bcdDevice", "0x100"}, {"bDeviceClass", "0x00"}, {"bDeviceSubClass", "0x00"},
                                        {"bDeviceProtocol", "0x00"}};
    for (unsigned i = 0; i < sizeof top / sizeof *top; ++i) snprintf(p, sizeof p, "%s/%s", g, top[i][0]), gadget_write(p, top[i][1]);
    static const char *const dirs[] = {"strings", "configs", "functions", "strings/0x409", "configs/c.1", "configs/c.1/strings",
                                       "configs/c.1/strings/0x409", "functions/uac2.a"};
    for (unsigned i = 0; i < sizeof dirs / sizeof *dirs; ++i) snprintf(p, sizeof p, "%s/%s", g, dirs[i]), mkdir(p, 0755);
    static const char *const attrs[][2] = {
        {"strings/0x409/manufacturer", "Shanling"}, {"strings/0x409/product", "Q2"}, {"configs/c.1/MaxPower", "20"},
        {"configs/c.1/bmAttributes", "0x80"}, {"configs/c.1/strings/0x409/configuration", "uac2"},
        {"functions/uac2.a/if_ctrl_name", "Q2"}, {"functions/uac2.a/c_chmask", "0x03"}, {"functions/uac2.a/c_ssize", "4"},
        {"functions/uac2.a/c_srate", "32000,44100,48000,64000,88200,96000,128000,176400,192000,352800,384000"},
        {"functions/uac2.a/c_hs_bint", "2"}, {"functions/uac2.a/req_number", "32"}, {"functions/uac2.a/p_chmask", "0"},
        {"functions/uac2.a/c_sync", "async"}, {"functions/uac2.a/c_mute_present", "0"}, {"functions/uac2.a/c_volume_present", "0"},
        {"functions/uac2.a/p_mute_present", "0"}, {"functions/uac2.a/p_volume_present", "0"}};
    for (unsigned i = 0; i < sizeof attrs / sizeof *attrs; ++i)
        if (snprintf(p, sizeof p, "%s/%s", g, attrs[i][0]), gadget_write(p, attrs[i][1])) return -1;
    char link[400], target[400];
    snprintf(link, sizeof link, "%s/configs/c.1/uac2.a", g);
    snprintf(target, sizeof target, "%s/functions/uac2.a", g);
    if (symlink(target, link) && errno != EEXIST) return -1;
    return bind_udc(g);
}

static void dac_gadget_remove(void) {
    char g[300], p[400];
    snprintf(g, sizeof g, "%s/usb_gadget/q2dac", path("/sys/kernel/config"));
    snprintf(p, sizeof p, "%s/UDC", g), gadget_write(p, "\n");
    snprintf(p, sizeof p, "%s/configs/c.1/uac2.a", g), unlink(p);
    static const char *const dirs[] = {"functions/uac2.a", "configs/c.1/strings/0x409", "configs/c.1", "strings/0x409", ""};
    for (unsigned i = 0; i < sizeof dirs / sizeof *dirs; ++i) snprintf(p, sizeof p, "%s/%s", g, dirs[i]), rmdir(p);
}

/* the card to the computer: nothing may hold it, so the player is stopped and the card unmounted first; adb's gadget
 * parked (it has the controller otherwise). Any step that fails puts back what was done. */
static int usb_set_adb(void);

static int usb_set_storage(void) {
    if (usb_mode == USB_STORAGE) return 0;
    if (usb_mode != USB_ADB) usb_set_adb();
    if (find_card(card_dev, sizeof card_dev)) return say("no card mounted"), -1;
    ask("/run/vamp/play.sock", "Q2PLAY/1", "stop");
    run_tool("/bin/sync");
    if (run_tool("/bin/umount /mnt/mmc")) return say("the card is busy"), -1;
    run_tool("/usr/bin/q2adb-gadget park");
    if (storage_gadget(card_dev)) {
        storage_gadget_remove();
        run_tool("/bin/mount %s /mnt/mmc", card_dev), run_tool("/usr/bin/q2adb-gadget unpark");
        return say("the storage gadget failed"), -1;
    }
    usb_mode = USB_STORAGE;
    say("USB: the card is the computer's (%s)", card_dev);
    tell("usb storage");
    return 0;
}

/* the Q2 the computer's DAC: the player stopped (the DAC is the loop's), adb's gadget parked, the UAC2 gadget bound and
 * the loop's service up. Any step that fails puts back what was done. */
static int usb_set_dac(void) {
    if (usb_mode == USB_DAC) return 0;
    if (usb_mode != USB_ADB) usb_set_adb();
    ask("/run/vamp/play.sock", "Q2PLAY/1", "stop");
    run_tool("/usr/bin/q2adb-gadget park");
    if (dac_gadget()) {
        dac_gadget_remove();
        run_tool("/usr/bin/q2adb-gadget unpark");
        return say("the USB DAC gadget failed"), -1;
    }
    run_tool("/usr/bin/sv up /etc/sv/q2dacloop");
    usb_mode = USB_DAC;
    say("USB: the Q2 is the computer's DAC");
    tell("usb dac");
    return 0;
}

/* Car Audio (docs/car-audio-os.md): q2car makes the iPhone gadget and starts q2-iap, which parks and unparks adb's
 * gadget itself and writes what it decided to /tmp/q2car.state (verdict=car|computer|silent|failed, then down=1 once
 * its gadget is gone). car_poll follows it. */
static int usb_set_car(void) {
    if (usb_mode == USB_CAR) return 0;
    if (usb_mode != USB_ADB) usb_set_adb();
    ask("/run/vamp/play.sock", "Q2PLAY/1", "pause"); /* the song moves to the car's PCM itself if it is a car; nothing jumps */
    if (run_tool("/usr/bin/q2car start")) return say("q2car did not start"), -1;
    usb_mode = USB_CAR, car_told = 0, car_auto = 0;
    say("USB: shown as an iPhone, waiting for q2-iap's verdict");
    tell("usb car");
    return 0;
}

/* while the iPhone is shown, twice a second: a car stays; anything else, once q2car has removed its gadget, is adb,
 * except a computer while it was shown for Automatic, which gets the card (Transfer Files) as the payload's does */
static void car_poll(void) {
    char raw[512] = "", *v;
    FILE *f = fopen(path("/tmp/q2car.state"), "r");
    size_t n = f ? fread(raw, 1, sizeof raw - 1, f) : 0;
    if (f) fclose(f);
    raw[n] = 0;
    if (!(v = strstr(raw, "verdict="))) return;
    v += 8;
    if (!strncmp(v, "car", 3) && (v[3] == '\n' || !v[3])) {
        if (!car_told) say("USB: a car"), tell("car"), car_told = 1;
    } else if (strstr(raw, "down=1")) {
        say("USB: not a car (%.*s)", (int)strcspn(v, "\n"), v);
        usb_mode = USB_ADB;
        if (car_auto && !strncmp(v, "computer", 8) && !usb_set_storage()) return;
        say("USB: adb");
        tell("usb adb");
    }
}

static int usb_set_adb(void) {
    if (usb_mode == USB_ADB) return 0;
    if (usb_mode == USB_CAR) {
        run_tool("/usr/bin/q2car stop"); /* q2-iap ended, the gadget gone, adb's unparked */
        usb_mode = USB_ADB;
        say("USB: adb");
        tell("usb adb");
        return 0;
    }
    if (usb_mode == USB_DAC) {
        run_tool("/usr/bin/sv down /etc/sv/q2dacloop"); /* the loop closes the gadget's PCM before the gadget goes */
        dac_gadget_remove();
    } else {
        storage_gadget_remove();
        if (run_tool("/bin/mount %s /mnt/mmc", card_dev)) say("the card did not mount again");
    }
    run_tool("/usr/bin/q2adb-gadget unpark");
    usb_mode = USB_ADB;
    say("USB: adb");
    tell("usb adb");
    return 0;
}

/* ---- the cable ---- */

/* The USB setting's values, [Q2POD] USBPICK as the payload's USB picker saves them (patch/usb_ui.c); anything else is
 * Automatic. USB Debugging is adb, which is the mode with no cable already. */
enum { PICK_AUTO, PICK_CHARGE, PICK_FILES, PICK_AUDIO, PICK_DEBUG, PICK_ASK, PICKS };
static int cable_in = -1; /* ac/online at the last read: -1 before the first, so a cable in at the start is a plug */

static void cable_poll(void) {
    int v;
    if (read_int(path("/sys/class/power_supply/ac/online"), &v)) return;
    v = v != 0;
    if (v == cable_in) return;
    cable_in = v;
    power_fresh = 1; /* stock's get_battery_capacity at a change of usb_detect, in or out */
    read_battery(); /* cable= told at once */
    if (!v) {
        say("cable out");
        usb_set_adb();
        return;
    }
    int pick = config_int("Q2POD", "USBPICK", PICK_AUTO);
    if (pick < 0 || pick >= PICKS) pick = PICK_AUTO;
    say("cable in, USBPICK %d", pick);
    if (pick == PICK_AUTO) {
        if (!usb_set_car()) car_auto = 1;
    } else if (pick == PICK_FILES) usb_set_storage();
    else if (pick == PICK_AUDIO) usb_set_dac();
    else if (pick == PICK_ASK) tell("usb ask"); /* the chooser is the UI's; until it answers, Charge Only */
}

/* ---- the watchdog (docs/sysd.md#the-watchdog) ----
 *
 * The X2000's watchdog (soc_watchdog.ko's /dev/jz_watchdog), which stock never starts, through the driver's own ioctls
 * as today's Vamp drives it (patch/system.c, docs/internals.md#watchdog): started at WDT_MS, fed each second from this
 * loop, so a kernel or a q2sysd stalled that long restarts the Q2. Its count runs through a suspend, so it is stopped
 * across Standby; its close is a no-op, so a clean end (a power off, a restart, SIGTERM) stops it first. */
#define WDT_DEV "/dev/jz_watchdog"
#define WDT_START 0x8004570d /* an int of milliseconds, by pointer */
#define WDT_STOP 0x2000570e
#define WDT_FEED 0x2000570f
#define WDT_MS 60000
static int wdt_fd = -1, wdt_on, wdt_said;

static void wdt_start(void) {
    if (wdt_fd < 0 && (wdt_fd = open(path(WDT_DEV), O_RDWR | O_CLOEXEC)) < 0) {
        if (!wdt_said++) say("no watchdog (%s: %s)", WDT_DEV, strerror(errno));
        return;
    }
    int ms = WDT_MS;
    if (ioctl(wdt_fd, WDT_START, &ms)) {
        if (!wdt_said++) say("the watchdog refused its start (%s)", strerror(errno));
        return;
    }
    if (!wdt_said++) say("watchdog: %d s", WDT_MS / 1000);
    wdt_on = 1;
}

static void wdt_feed(void) {
    if (wdt_on) ioctl(wdt_fd, WDT_FEED);
}

static void wdt_stop(void) {
    if (wdt_on) ioctl(wdt_fd, WDT_STOP);
    wdt_on = 0;
}

/* ---- Standby ---- */

static int card_boot;

/* The screen off, the music paused, Wi-Fi off (not on a card boot, where it is the developer's way in), and the
 * kernel's suspend: the write to /sys/power/state returns when a wake source has resumed it. Then the screen on and Wi-Fi
 * woken. */
static int standby(void) {
    int was_on = screen_on;
    if (screen_on) set_screen(0);
    ask("/run/vamp/play.sock", "Q2PLAY/1", "pause");
    if (!card_boot) ask("/run/vamp/net.sock", "Q2NET/1", "off");
    run_tool("/bin/sync");
    say("standby");
    tell("standby");
    int dog = wdt_on;
    wdt_stop(); /* it would count through the suspend */
    int r = write_text(path("/sys/power/state"), "mem");
    if (dog) wdt_start();
    say(r ? "standby refused by the kernel" : "resumed");
    if (was_on || !r) set_screen(1);
    if (!card_boot) ask("/run/vamp/net.sock", "Q2NET/1", "wake");
    heat_slept = 1; /* the die's last reading is too old to hold the next to (q2guard_core.c) */
    tell("resumed");
    return r;
}

/* ---- the power key ---- */

static int in_fd[INPUTS], n_in, n_power; /* every input device (its use holds the screen lit); n_power of them have the power key */
static unsigned long long pressed_at; /* 0: not held */

static int has_power_key(int fd) {
    unsigned char bits[KEY_MAX / 8 + 1] = {0};
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof bits), bits) < 0) return 1; /* a stand-in that answers no ioctl: watched */
    return (bits[KEY_POWER / 8] >> (KEY_POWER % 8)) & 1;
}

static void open_inputs(void) {
    for (int i = 0; i < n_in; ++i) close(in_fd[i]);
    n_in = n_power = 0;
    DIR *d = opendir(path("/dev/input"));
    for (struct dirent *e; d && (e = readdir(d)) && n_in < INPUTS;) {
        if (strncmp(e->d_name, "event", 5)) continue;
        char p[440];
        snprintf(p, sizeof p, "%s/%s", path("/dev/input"), e->d_name);
        int fd = open(p, O_RDWR | O_NONBLOCK | O_CLOEXEC); /* RDWR: a test's FIFO stays open */
        if (fd < 0) fd = open(p, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        n_power += has_power_key(fd);
        in_fd[n_in++] = fd;
    }
    if (d) closedir(d);
}

static void shut(const char *cmd) {
    pressed_at = 0;
    wdt_stop(); /* a clean end: the shutdown is not a stall */
    pid_t p = fork();
    if (p == 0) {
        execl("/bin/sh", "sh", "-c", cmd, (char *)0);
        _exit(127);
    }
    /* not waited for: the system goes down (SIGCHLD stays default, which system()'s own wait needs) */
}

static void power_off(const char *why) {
    say("%s: powering off", why);
    tell("power off");
    shut(poweroff_cmd);
}

static void reboot_now(void) {
    say("the power key held in the charge wait: restarting");
    tell("reboot");
    shut(reboot_cmd);
}

static void read_input(int fd) {
    struct input_event e[16];
    ssize_t got;
    while ((got = read(fd, e, sizeof e)) > 0)
        for (unsigned i = 0; i < (size_t)got / sizeof *e; ++i) {
            if (e[i].type == EV_KEY || e[i].type == EV_ABS || e[i].type == EV_REL) use_now(); /* any use */
            if (e[i].type != EV_KEY || e[i].code != KEY_POWER) continue;
            if (e[i].value == 1) pressed_at = now_ms() | 1;
            else if (e[i].value == 0 && pressed_at) {
                pressed_at = 0;
                set_screen(!screen_on);
                read_battery();
            }
        }
}

/* ---- Q2SYS/1 ---- */

static struct client { int fd, hello, watch; char in[256]; size_t n; } cl[CLIENTS];
static int api_fd = -1;

static void client_say(struct client *c, const char *fmt, ...) {
    char line[320];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    if (send(c->fd, line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT) != n) close(c->fd), c->fd = -1;
}

static void tell(const char *fmt, ...) {
    char line[300];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    for (int i = 0; i < CLIENTS; ++i)
        if (cl[i].fd >= 0 && cl[i].watch) client_say(&cl[i], "%s", line);
}

static void client_line(struct client *c, const char *line) {
    int v;
    if (!c->hello) {
        if (strcmp(line, "Q2SYS/1")) { client_say(c, "err version"); close(c->fd), c->fd = -1; return; }
        char who[100];
        if (!q2perm_allowed(c->fd, path("/etc/passwd"), path("/etc/group"), "settings", who, sizeof who)) {
            say("refused %s: no settings permission", who); /* docs/platform/packages.md#users-and-groups */
            client_say(c, "err permission");
            close(c->fd), c->fd = -1;
            return;
        }
        c->hello = 1;
        client_say(c, "Q2SYS/1 ok");
    } else if (!strcmp(line, "caps")) { /* Vamp API 1's capability words (docs/platform/README.md#capabilities) */
        client_say(c, "caps screen awake brightness usb usb-dac usb-car standby heat jack battery watch");
    } else if (!strcmp(line, "battery")) {
        char text[160];
        read_battery();
        battery_text(text, sizeof text, " ", 1);
        client_say(c, "battery %s", text);
    } else if (!strcmp(line, "screen")) {
        client_say(c, "screen %s", screen_on ? "on" : "off");
    } else if (!strcmp(line, "screen on") || !strcmp(line, "screen off") || !strcmp(line, "screen toggle")) {
        int on = line[7] == 't' ? !screen_on : line[8] == 'n';
        client_say(c, on == screen_on || !set_screen(on) ? "ok" : "err screen");
    } else if (!strcmp(line, "jack")) { /* the two jacks, 1 plugged, 0 not, -1 not read (docs/sysd.md#the-jacks) */
        client_say(c, "jack po=%d bal=%d", jack_in[0], jack_in[1]);
    } else if (!strcmp(line, "heat")) { /* the guardian's state (docs/sysd.md#heat) */
        client_say(c, "heat %s", heat_on ? guard_state(&guard) : "off");
    } else if (!strcmp(line, "heat cancel")) { /* the load warning's Cancel, from a UI that is not Q2UI/1's */
        heat_cancel = 1;
        client_say(c, "ok");
    } else if (!strcmp(line, "awake")) { /* a use, as a key is: the screen off time starts again */
        use_now();
        client_say(c, "ok");
    } else if (sscanf(line, "brightness %d", &v) == 1) {
        client_say(c, set_brightness(v) ? "err brightness" : "ok");
    } else if (!strcmp(line, "usb")) {
        client_say(c, "usb %s", USB_NAMES[usb_mode]);
    } else if (!strcmp(line, "usb storage") || !strcmp(line, "usb adb") || !strcmp(line, "usb dac") || !strcmp(line, "usb car")) {
        int r = line[4] == 's' ? usb_set_storage() : line[4] == 'd' ? usb_set_dac() : line[4] == 'c' ? usb_set_car() : usb_set_adb();
        client_say(c, r ? "err usb" : "ok");
    } else if (!strcmp(line, "standby")) {
        client_say(c, "ok"); /* answered first: the write that suspends returns only at the resume */
        standby();
    } else if (!strcmp(line, "watch")) {
        c->watch = 1;
        client_say(c, "ok");
    } else {
        client_say(c, "err %s", line);
    }
}

static void client_read(struct client *c) {
    ssize_t got = recv(c->fd, c->in + c->n, sizeof c->in - 1 - c->n, 0);
    if (got <= 0) { close(c->fd), c->fd = -1; return; }
    c->n += (size_t)got;
    char *p = c->in, *nl;
    while (c->fd >= 0 && (nl = memchr(p, '\n', c->n - (size_t)(p - c->in)))) {
        *nl = 0;
        if (nl > p && nl[-1] == '\r') nl[-1] = 0;
        client_line(c, p);
        p = nl + 1;
    }
    if (c->fd < 0) return;
    c->n -= (size_t)(p - c->in);
    memmove(c->in, p, c->n);
    if (c->n >= sizeof c->in - 1) close(c->fd), c->fd = -1;
}

/* ---- a low battery (docs/sysd.md#a-low-battery) ----
 *
 * Stock's check_lowbattery_state (0x4f3b38, once a second from on_wm_idle_status) on the latest reading: with no cable,
 * LOW_SECONDS readings in a row at LOW_MV or under, or at 0%, power the Q2 off ("Shutdown", 0x4f1a54, then
 * poweroff_page); a first reading under LOW_WARN lights the screen and says "Low Battery" (on_wm_timer_dialog), once
 * until a reading is LOW_WARN again. Any other reading starts the count again. Below LOW_FLOOR the battery is read each
 * second, as today's Vamp reads it there (BATT_DARK_FLOOR, docs/internals.md#battery). */
#define LOW_MV 3400
#define LOW_WARN 16
#define LOW_FLOOR 20
#define LOW_SECONDS 5
static int low_count, low_warned;
static void ui_say(const char *line);

static void low_poll(void) {
    if (bat.level < 0 || bat.mv < 0) return; /* a read that failed: nothing to go by */
    if (cable_in != 1) {
        if (bat.mv <= LOW_MV || bat.level == 0) {
            if (++low_count < LOW_SECONDS) return;
            low_count = 0;
            say("battery %d%% %d mV: shutting down", bat.level, bat.mv);
            tell("lowbattery off");
            ui_say("toast Shutdown");
            power_off("the battery is empty");
            return;
        }
        if (!low_warned) {
            if (bat.level < LOW_WARN) {
                low_warned = 1;
                say("battery %d%%: low", bat.level);
                tell("lowbattery warn");
                if (!screen_on) set_screen(1);
                ui_say("toast Low%20Battery");
            }
            low_count = 0;
            return;
        }
    }
    if (low_warned && bat.level >= LOW_WARN) low_warned = 0;
    low_count = 0;
}

/* ---- Auto Standby and Auto Power Off (docs/sysd.md#auto-standby-and-auto-power-off) ---- */

/* q2playd's now file says a song is playing (state=1) */
static int music_playing(void) {
    char b[2700];
    FILE *f = fopen(path("/tmp/coda-rc.now"), "r");
    size_t n = f ? fread(b, 1, sizeof b - 1, f) : 0;
    if (f) fclose(f);
    b[n] = 0;
    return !strncmp(b, "state=1\n", 8) || strstr(b, "\nstate=1\n") != 0;
}

/* What stock's check_to_poweroption (0x4f3c90) waits on, each second: neither counts while music plays (Q2PLAY/1's
 * now file, which an AirPlay or DLNA song also reaches) or while Car Audio or USB storage has the cable, and Standby not
 * while a cable is in at all (g_charge_state is usb_detect's cable, not the charge). A hold stops a count where it is;
 * only a use or a cable change makes it whole again. */
static long long standby_left, savepower_left; /* ms, already scaled */
static unsigned long long ticked_at;
static int charge_wait; /* Auto Power Off came with a cable in: waiting on the charge, as stock's poweroff_page does */

static int power_held(void) { return music_playing() || usb_mode == USB_CAR || usb_mode == USB_STORAGE; }

static void idle_poll(unsigned long long t) {
    long long dt = ticked_at ? (long long)(t - ticked_at) * time_scale : 0;
    ticked_at = t;
    if (charge_wait) {
        read_battery();
        if (cable_in != 1 || !strcmp(bat.status, "Full")) charge_wait = 0, power_off(cable_in != 1 ? "the charge wait's cable out" : "the charge wait's Full");
        return;
    }
    if (power_fresh) standby_left = standby_s * 1000ll, savepower_left = savepower_s * 1000ll, power_fresh = 0;
    if (!standby_s && !savepower_s) return;
    if (power_held()) return;
    if (savepower_s && (savepower_left -= dt) <= 0) {
        power_fresh = 1;
        if (cable_in != 1) {
            power_off("unused, auto power off");
            return;
        }
        /* stock's into_poweroff, then the page's own wait: the music and the radios stopped, on until Full or the
         * cable out; a press still lights the screen and a hold restarts (device_reboot) */
        say("unused, auto power off with a cable in: the charge wait");
        ask("/run/vamp/play.sock", "Q2PLAY/1", "pause");
        if (!card_boot) ask("/run/vamp/net.sock", "Q2NET/1", "off");
        charge_wait = 1;
        tell("charge wait");
        return;
    }
    if (standby_s && cable_in != 1 && (standby_left -= dt) <= 0) {
        say("unused %d s: auto standby", standby_s);
        standby();
        use_now(); /* stock's device_standby: the times whole from the resume */
    }
}

/* ---- the jacks (docs/sysd.md#the-jacks) ----
 * Stock's check_headset_status (0x4f335c, once a second): PA07 the 3.5 mm jack, PA08 the 4.4 mm, 0 plugged, read here
 * through the kernel's GPIO class (port A's base is 0). A plug counts at once; an unplug only when a read at least
 * JACK_UNPLUG_MS after the first unplugged one still says so (today's Vamp's jack_detect.c: powering the DAC up reads
 * PA07 unplugged for about 120 ms with a cable in). At an unplug that counts, music playing is paused, as stock's
 * config_po_output and config_bal_output do whatever the output. */
#define JACK_UNPLUG_MS 300
static const int JACK_GPIO[2] = {7, 8};
static const char *const JACK_WORD[2] = {"po", "bal"}, *const JACK_NAME[2] = {"3.5 mm", "4.4 mm"};
static int jack_in[2] = {-1, -1};              /* 1 plugged, 0 not, -1 not read yet */
static unsigned long long jack_out_at[2];      /* the first unplugged read not yet counted, 0 none */

static int gpio_in(int n, int *v) {
    char p[300];
    snprintf(p, sizeof p, "%s/gpio%d/value", path("/sys/class/gpio"), n);
    if (!read_int(p, v)) return 0;
    char num[8];
    snprintf(num, sizeof num, "%d", n);
    write_text(path("/sys/class/gpio/export"), num); /* not exported yet: it stays an input, as it comes */
    return read_int(p, v);
}

static void jack_poll(unsigned long long t) {
    for (int i = 0; i < 2; ++i) {
        int raw;
        if (gpio_in(JACK_GPIO[i], &raw)) continue;
        int plugged = raw == 0;
        if (jack_in[i] < 0) {
            jack_in[i] = plugged;
            say("%s jack %s", JACK_NAME[i], plugged ? "plugged" : "unplugged");
        } else if (plugged) {
            if (jack_out_at[i]) say("%s jack: an unplugged read that did not last, ignored", JACK_NAME[i]);
            jack_out_at[i] = 0;
            if (!jack_in[i]) jack_in[i] = 1, say("%s jack plugged", JACK_NAME[i]), tell("jack %s 1", JACK_WORD[i]);
        } else if (jack_in[i] && !jack_out_at[i]) {
            jack_out_at[i] = t | 1;
        } else if (jack_in[i] && t >= jack_out_at[i] + JACK_UNPLUG_MS) {
            jack_in[i] = 0, jack_out_at[i] = 0;
            say("%s jack unplugged", JACK_NAME[i]);
            tell("jack %s 0", JACK_WORD[i]);
            if (music_playing()) ask("/run/vamp/play.sock", "Q2PLAY/1", "pause");
        }
    }
}

/* ---- heat (docs/sysd.md#heat): today's Vamp's overheat guardian (patch/q2guard_core.c, q2guard_pmic.c), its
 * device side and demo's side in one. A sample every GUARD_TICK_MS: the AXP2101's die through its I2C window (on a
 * thread of its own, as each window waits 1 s), the CPU's busy share, the charger's state; guard_step decides. Its
 * events are told to watchers (heat warn|nosensor|ok|loadwarn left=S|cancelled|off) and shown through Q2UI/1 when
 * q2ui serves it; a power-off pauses the music and powers off GUARD_CARD_MS later, as demo's card does. On a card
 * boot today's Vamp's own q2guard runs beside us and owns the PMIC's window: nothing here then. */
#define GUARD_TICK_MS 5000
#define PMIC_DEV "/dev/i2c-6"
#define PMIC_ADDR 0x34
#define I2C_SLAVE_FORCE 0x0706
#define I2C_SMBUS 0x0720
#define I2C_SMBUS_BYTE_DATA 2
int card_mounted(void) { return 0; } /* card_store.c's question: q2sysd keeps no heat log */
static char heat_file[128];          /* --heat-file: a temperature in tenths, or "none", in place of the PMIC */
static struct guard guard;
static struct pmic pmic;
static int pmic_fd = -1, heat_die = GUARD_NONE, heat_cancel, heat_slept, heat_on;
static pthread_mutex_t heat_mu = PTHREAD_MUTEX_INITIALIZER;
static unsigned long long heat_at, heat_off_ms;
static unsigned long long cpu_all, cpu_idle;

struct smbus_args { char read_write; unsigned char command; unsigned size; unsigned char *data; };
static int smbus(int rw, int reg, unsigned char *d) {
    struct smbus_args a = {(char)rw, (unsigned char)reg, I2C_SMBUS_BYTE_DATA, d};
    return ioctl(pmic_fd, I2C_SMBUS, &a);
}
static int bus_rd(void *c, int reg) { (void)c; unsigned char d[34]; return smbus(1, reg, d) < 0 ? -1 : d[0]; }
static int bus_wr(void *c, int reg, int v) { (void)c; unsigned char d[34] = {(unsigned char)v}; return smbus(0, reg, d) < 0 ? -1 : 0; }
static void bus_sleep(void *c, int ms) { (void)c, usleep((unsigned)ms * 1000u); }
static const struct pmic_bus pmic_bus = {bus_rd, bus_wr, bus_sleep, 0};

static void *pmic_thread(void *arg) {
    (void)arg;
    for (;;) {
        int adc = -1, t = pmic_sample(&pmic_bus, &pmic, &adc);
        if (pmic.note[0]) say("%s", pmic.note), pmic.note[0] = 0;
        pthread_mutex_lock(&heat_mu);
        heat_die = t;
        pthread_mutex_unlock(&heat_mu);
        if (pmic.state == PMIC_OFF) return 0;
        usleep((GUARD_TICK_MS - PMIC_WINDOW_MS) * 1000u / (unsigned)time_scale);
    }
}

static void heat_start(void) {
    if (heat_file[0]) { heat_on = 1; return; }
    if (card_boot) { say("heat: a card boot, today's Vamp's q2guard guards"); return; }
    pmic_fd = open(path(PMIC_DEV), O_RDWR | O_CLOEXEC);
    if (pmic_fd < 0 || ioctl(pmic_fd, I2C_SLAVE_FORCE, PMIC_ADDR) < 0) {
        say("heat: %s %s: the CPU's load watched instead", PMIC_DEV, pmic_fd < 0 ? "does not open" : "refuses 0x34");
        pmic.state = PMIC_OFF;
    } else {
        pthread_t th;
        if (pthread_create(&th, 0, pmic_thread, 0)) pmic.state = PMIC_OFF; else pthread_detach(th);
    }
    heat_on = 1;
}

static int busy_now(void) {
    char b[512];
    FILE *f = fopen(path("/proc/stat"), "r");
    size_t n = f ? fread(b, 1, sizeof b - 1, f) : 0;
    if (f) fclose(f);
    b[n] = 0;
    unsigned long long v[8] = {0}, all = 0;
    if (strncmp(b, "cpu ", 4)) return -1;
    const char *p = b + 4;
    for (int i = 0; i < 8; ++i) { while (*p == ' ') ++p; while (*p >= '0' && *p <= '9') v[i] = v[i] * 10 + (unsigned)(*p++ - '0'); }
    for (int i = 0; i < 8; ++i) all += v[i];
    unsigned long long idle = v[3] + v[4], da = all - cpu_all, di = idle - cpu_idle;
    int first = !cpu_all;
    cpu_all = all, cpu_idle = idle;
    return first || !da ? -1 : (int)(100 - di * 100 / da);
}

static void ui_say(const char *line); /* Q2UI/1, when q2ui serves it */

static void heat_poll(unsigned long long t) {
    if (!heat_on || t < heat_at + GUARD_TICK_MS / (unsigned)time_scale) return;
    heat_at = t;
    int temp = GUARD_NONE;
    if (heat_file[0]) {
        char b[16] = "";
        FILE *f = fopen(path(heat_file), "r");
        if (f && fgets(b, sizeof b, f)) temp = b[0] == '-' || (b[0] >= '0' && b[0] <= '9') ? atoi(b) : GUARD_NONE;
        if (f) fclose(f);
    } else {
        pthread_mutex_lock(&heat_mu);
        temp = heat_die;
        pthread_mutex_unlock(&heat_mu);
    }
    struct guard_in in = {(unsigned)(t * (unsigned)time_scale), temp, busy_now(), heat_cancel, guard.off, 1,
                          (cable_in == 1 ? GUARD_CHARGE_CABLE : 0) | (!strcmp(bat.status, "Charging") ? GUARD_CHARGE_CHARGING : 0),
                          heat_slept};
    heat_cancel = heat_slept = 0;
    struct guard_out o;
    guard_step(&guard, &in, &o);
    switch (o.event) {
    case EV_WARN: say("heat: warm (%d.%d C)", temp / 10, temp % 10), tell("heat warn"), ui_say("toast Q2%20is%20getting%20hot"); break;
    case EV_SENSOR_DOWN:
        say("heat: no sensor usable: %s", guard_why(&guard)), tell("heat nosensor");
        ui_say("ask heat-sensor No%20Temperature%20Sensor Overheat%20protection%20watches%20the%20load%20instead OK");
        break;
    case EV_SENSOR_OK: say("heat: the sensor is back"), tell("heat ok"), ui_say("withdraw heat-sensor"); break;
    case EV_LOAD_WARN:
        say("heat: working hard with no sensor"), tell("heat loadwarn left=%d", GUARD_LOAD_MS / 1000);
        ui_say("ask heat-load Q2%20has%20been%20working%20hard It%20shuts%20down%20in%2060%20s%20to%20cool%20off Cancel Shut%20Down");
        break;
    case EV_LOAD_CANCELLED: say("heat: the load warning cancelled"), tell("heat cancelled"), ui_say("withdraw heat-load"); break;
    case EV_OFF:
        say("heat: %s: powering off in %d s", guard.off_why == 'L' ? "the load with no sensor" : "too hot", GUARD_CARD_MS / 1000);
        ask("/run/vamp/play.sock", "Q2PLAY/1", "pause");
        tell("heat off");
        ui_say("toast Q2%20is%20too%20hot.%20Shutting%20down%20to%20cool%20off.");
        heat_off_ms = t + GUARD_CARD_MS / (unsigned)time_scale;
        break;
    }
    static int powered;
    if (!powered && (o.power_off || (heat_off_ms && t >= heat_off_ms))) powered = 1, heat_off_ms = 0, power_off("heat");
}

/* Q2UI/1 (docs/platform/notices.md), held open while q2ui serves it: a line sent, an ask's answer read later in the
 * loop. Without the socket a line goes nowhere: the watchers' heat lines are told anyway. */
static int ui_fd = -1;
static char ui_in[256];
static size_t ui_n;

static void ui_say(const char *line) {
    if (ui_fd < 0) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/ui.sock"));
        ui_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (ui_fd < 0 || connect(ui_fd, (struct sockaddr *)&a, sizeof a)) {
            if (ui_fd >= 0) close(ui_fd), ui_fd = -1;
            return;
        }
        ui_n = 0;
        if (send(ui_fd, "Q2UI/1\n", 7, MSG_NOSIGNAL) != 7) { close(ui_fd), ui_fd = -1; return; }
    }
    char b[300];
    int n = snprintf(b, sizeof b, "%s\n", line);
    if (n < 0 || n >= (int)sizeof b || send(ui_fd, b, (size_t)n, MSG_NOSIGNAL) != n) close(ui_fd), ui_fd = -1;
}

/* the UI's lines: its oks, and an ask's answer (heat-load 1 Cancel, 2 Shut Down) */
static void ui_read(void) {
    ssize_t got = recv(ui_fd, ui_in + ui_n, sizeof ui_in - 1 - ui_n, MSG_DONTWAIT);
    if (got <= 0) {
        if (got == 0 || (errno != EAGAIN && errno != EWOULDBLOCK)) close(ui_fd), ui_fd = -1;
        return;
    }
    ui_n += (size_t)got;
    ui_in[ui_n] = 0;
    for (char *nl; (nl = strchr(ui_in, '\n'));) {
        *nl = 0;
        if (!strcmp(ui_in, "answer heat-load 1")) heat_cancel = 1;
        else if (!strcmp(ui_in, "answer heat-load 2") && !guard.off) {
            say("heat: Shut Down on the load warning");
            ask("/run/vamp/play.sock", "Q2PLAY/1", "pause");
            power_off("heat, Shut Down");
        }
        ui_n -= (size_t)(nl + 1 - ui_in);
        memmove(ui_in, nl + 1, ui_n + 1);
    }
    if (ui_n >= sizeof ui_in - 1) ui_n = 0, ui_in[0] = 0;
}

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--poweroff") && i + 1 < argc) snprintf(poweroff_cmd, sizeof poweroff_cmd, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--heat-file") && i + 1 < argc) snprintf(heat_file, sizeof heat_file, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--reboot") && i + 1 < argc) snprintf(reboot_cmd, sizeof reboot_cmd, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--card-boot")) card_boot = 1;
        else if (!strcmp(argv[i], "--time-scale") && i + 1 < argc && atoi(argv[i + 1]) > 0) time_scale = atoi(argv[++i]);
        else return fprintf(stderr, "usage: q2sysd [--root DIR] [--poweroff COMMAND] [--reboot COMMAND] [--card-boot] [--time-scale N] [--heat-file F]\n"), 2;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    for (int i = 0; i < CLIENTS; ++i) cl[i].fd = -1;
    mkdir(path("/run"), 0755);
    mkdir(path("/run/vamp"), 0755);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/sys.sock"));
    unlink(a.sun_path);
    api_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (api_fd < 0 || bind(api_fd, (struct sockaddr *)&a, sizeof a) || listen(api_fd, CLIENTS)) say("%s: %s", a.sun_path, strerror(errno));
    q2perm_socket(a.sun_path, path("/etc/group"), "settings"); /* 0660, the settings group's */
    charge_limit = config_int("Q2POD", "CHARGELIMIT", 0);
    find_backlight();
    open_inputs();
    read_battery();
    light_poll();
    say("%d input device(s), %d with the power key, backlight %s, screen %s", n_in, n_power, backlight[0] ? backlight : "none",
        screen_on ? "on" : "off");
    cable_poll();
    heat_start();
    wdt_start();
    unsigned long long battery_at = now_ms(), scanned_at = now_ms(), cable_at = now_ms();
    while (!stopping) {
        struct pollfd p[2 + INPUTS + CLIENTS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = api_fd, .events = POLLIN};
        for (int i = 0; i < n_in; ++i) p[n++] = (struct pollfd){.fd = in_fd[i], .events = POLLIN};
        for (int i = 0; i < CLIENTS; ++i) p[n++] = (struct pollfd){.fd = cl[i].fd, .events = POLLIN};
        int ui_at = n;
        p[n++] = (struct pollfd){.fd = ui_fd, .events = POLLIN}; /* -1 with none: poll passes over it */
        unsigned long long t = now_ms();
        long long wait = (long long)(battery_at + BATTERY_S * 1000ull) - (long long)t;
        if (pressed_at) {
            long long hold = (long long)(pressed_at + HOLD_MS) - (long long)t;
            if (hold < wait) wait = hold;
        }
        if (!n_power && (long long)(scanned_at + RESCAN_MS) - (long long)t < wait) wait = (long long)(scanned_at + RESCAN_MS) - (long long)t;
        if ((long long)(cable_at + CABLE_MS) - (long long)t < wait) wait = (long long)(cable_at + CABLE_MS) - (long long)t;
        if (usb_mode == USB_CAR && wait > 500) wait = 500;
        if (heat_on) {
            long long h = (long long)(heat_at + GUARD_TICK_MS / (unsigned)time_scale) - (long long)t;
            if (h < wait) wait = h;
        }
        if (wait < 0) wait = 0;
        if (poll(p, (nfds_t)n, (int)wait) < 0 && errno != EINTR) break;
        if (p[0].revents & POLLIN) {
            int fd = accept4(api_fd, 0, 0, SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < CLIENTS && cl[i].fd >= 0) ++i;
            if (fd >= 0 && i == CLIENTS) close(fd);
            else if (fd >= 0) cl[i] = (struct client){.fd = fd};
        }
        for (int i = 0; i < n_in; ++i)
            if (p[1 + i].revents) read_input(in_fd[i]);
        for (int i = 0; i < CLIENTS; ++i)
            if (cl[i].fd >= 0 && p[1 + n_in + i].fd == cl[i].fd && p[1 + n_in + i].revents) client_read(&cl[i]);
        if (ui_fd >= 0 && p[ui_at].fd == ui_fd && p[ui_at].revents) ui_read();
        t = now_ms();
        if (pressed_at && t >= pressed_at + HOLD_MS) {
            if (charge_wait) reboot_now();
            else power_off("the power key held");
        }
        if (t >= battery_at + BATTERY_S * 1000ull) read_battery(), battery_at = t;
        if (!n_power && t >= scanned_at + RESCAN_MS) open_inputs(), scanned_at = t;
        if (t >= cable_at + CABLE_MS) {
            wdt_feed();
            cable_poll(), cable_at = t;
            if (bat.level >= 0 && bat.level < LOW_FLOOR) read_battery(), battery_at = t;
            low_poll();
            light_poll();
            if (screen_on && light_s && t >= used_at + (unsigned long long)light_s * 1000) {
                say("unused %d s: the screen off", light_s);
                used_at = t; /* a screen that cannot go off is tried again a whole time later */
                set_screen(0);
            }
            idle_poll(t);
            jack_poll(t);
        }
        heat_poll(t);
        if (usb_mode == USB_CAR) car_poll();
    }
    wdt_stop();
    unlink(path("/run/vamp/sys.sock"));
    return 0;
}
