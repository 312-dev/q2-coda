/* q2sysd: Vamp OS's system service (docs/vamp-os.md#who-owns-what, docs/sysd.md), its first slice: the power key,
 * the screen's light, and the battery.
 *
 *   - The power key (KEY_POWER on any input device: md-gpio-keys' power button, the PMIC's key, and the touchscreen's
 *     double tap, which hyn_touch reports as KEY_POWER while its gesture mode is on): a press shorter than HOLD_MS
 *     turns the screen off or on; held HOLD_MS it powers the Q2 off (POWEROFF, init's shutdown: the services stopped,
 *     the file systems unmounted).
 *   - The screen: off is the backlight's bl_power 4 (FB_BLANK_POWERDOWN) and the touchscreen's gesture mode on, as
 *     stock's screen_action does (docs/internals.md#screen-off-and-wake); on is bl_power 0 and gestures off. q2ui
 *     reads the same bl_power and drops input while it is dark (ui/ui.c dark).
 *   - The battery: the CW2015's level and voltage, the charger's state, read every BATTERY_S and at each change of
 *     the screen, written to /run/vamp/battery and told to watchers.
 *
 *   - Charge Limit (Q2POD CHARGELIMIT in config.ini, as the payload's charge_poll): charging stopped at 80% and handed
 *     back at 75% through the charger's active-low enable line, PE22.
 *   - USB modes: adb (q2adb's gadget, the default) and storage (the card to the computer: the player stopped, the card
 *     unmounted, a mass-storage gadget as stock's storage_config.sh makes it), and back.
 *   - Standby: the screen off, the music paused, Wi-Fi off, then the kernel's suspend (/sys/power/state mem) until a
 *     wake source (the power key) resumes it; then the screen on.
 *
 * Q2SYS/1 on /run/vamp/sys.sock (docs/sysd.md#q2sys1): screen on|off|toggle, brightness P, battery, usb, usb adb|storage,
 * standby, watch.
 * --root prefixes every path and --poweroff names the command, for test/sysd.py. */
#define _GNU_SOURCE
#include <dirent.h>
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

#define HOLD_MS 3000    /* the power key held this long powers off */
#define BATTERY_S 30    /* the battery read this often */
#define INPUTS 8        /* input devices watched */
#define CLIENTS 8       /* Q2SYS/1 connections */
#define RESCAN_MS 10000 /* input devices looked for again this often while none has the power key */
#define CHARGE_STOP 80   /* Charge Limit's stop and resume levels (the payload's CHARGE_STOP, CHARGE_RESUME) */
#define CHARGE_RESUME 75
#define PE22 150         /* the charger's active-low enable: port E (gpiochip4, base 128) line 22 */

static char root[256], poweroff_cmd[256] = "/sbin/poweroff";

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
 * free-text fields are (docs/sysd.md#q2sys1), every byte below 0x21, '%', '"', '=' and 0x7f as %XX; in the file, one
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

static int usb_storage; /* the card is the computer's */
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
    char udc[256] = "";
    DIR *d = opendir(path("/sys/class/udc"));
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.') snprintf(udc, sizeof udc, "%s", e->d_name);
    if (d) closedir(d);
    snprintf(p, sizeof p, "%s/UDC", g);
    return *udc && !gadget_write(p, udc) ? 0 : -1;
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

/* the card to the computer: nothing may hold it, so the player is stopped and the card unmounted first; adb's gadget
 * parked (it has the controller otherwise). Any step that fails puts back what was done. */
static int usb_set_storage(void) {
    if (usb_storage) return 0;
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
    usb_storage = 1;
    say("USB: the card is the computer's (%s)", card_dev);
    tell("usb storage");
    return 0;
}

static int usb_set_adb(void) {
    if (!usb_storage) return 0;
    storage_gadget_remove();
    if (run_tool("/bin/mount %s /mnt/mmc", card_dev)) say("the card did not mount again");
    run_tool("/usr/bin/q2adb-gadget unpark");
    usb_storage = 0;
    say("USB: adb");
    tell("usb adb");
    return 0;
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
    int r = write_text(path("/sys/power/state"), "mem");
    say(r ? "standby refused by the kernel" : "resumed");
    if (was_on || !r) set_screen(1);
    if (!card_boot) ask("/run/vamp/net.sock", "Q2NET/1", "wake");
    tell("resumed");
    return r;
}

/* ---- the power key ---- */

static int in_fd[INPUTS], n_in;
static unsigned long long pressed_at; /* 0: not held */

static int has_power_key(int fd) {
    unsigned char bits[KEY_MAX / 8 + 1] = {0};
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof bits), bits) < 0) return 1; /* a stand-in that answers no ioctl: watched */
    return (bits[KEY_POWER / 8] >> (KEY_POWER % 8)) & 1;
}

static void open_inputs(void) {
    for (int i = 0; i < n_in; ++i) close(in_fd[i]);
    n_in = 0;
    DIR *d = opendir(path("/dev/input"));
    for (struct dirent *e; d && (e = readdir(d)) && n_in < INPUTS;) {
        if (strncmp(e->d_name, "event", 5)) continue;
        char p[440];
        snprintf(p, sizeof p, "%s/%s", path("/dev/input"), e->d_name);
        int fd = open(p, O_RDWR | O_NONBLOCK | O_CLOEXEC); /* RDWR: a test's FIFO stays open */
        if (fd < 0) fd = open(p, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        if (has_power_key(fd)) in_fd[n_in++] = fd;
        else close(fd);
    }
    if (d) closedir(d);
}

static void power_off(void) {
    say("power key held %d ms: powering off", HOLD_MS);
    tell("power off");
    pressed_at = 0;
    pid_t p = fork();
    if (p == 0) {
        execl("/bin/sh", "sh", "-c", poweroff_cmd, (char *)0);
        _exit(127);
    }
    /* not waited for: the system goes down (SIGCHLD stays default, which system()'s own wait needs) */
}

static void read_input(int fd) {
    struct input_event e[16];
    ssize_t got;
    while ((got = read(fd, e, sizeof e)) > 0)
        for (unsigned i = 0; i < (size_t)got / sizeof *e; ++i) {
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
        c->hello = 1;
        client_say(c, "Q2SYS/1 ok");
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
    } else if (sscanf(line, "brightness %d", &v) == 1) {
        client_say(c, set_brightness(v) ? "err brightness" : "ok");
    } else if (!strcmp(line, "usb")) {
        client_say(c, "usb %s", usb_storage ? "storage" : "adb");
    } else if (!strcmp(line, "usb storage") || !strcmp(line, "usb adb")) {
        client_say(c, (line[4] == 's' ? usb_set_storage() : usb_set_adb()) ? "err usb" : "ok");
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

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--poweroff") && i + 1 < argc) snprintf(poweroff_cmd, sizeof poweroff_cmd, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--card-boot")) card_boot = 1;
        else return fprintf(stderr, "usage: q2sysd [--root DIR] [--poweroff COMMAND] [--card-boot]\n"), 2;
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
    chmod(a.sun_path, 0600);
    charge_limit = config_int("Q2POD", "CHARGELIMIT", 0);
    find_backlight();
    open_inputs();
    read_battery();
    say("%d input device(s) with the power key, backlight %s, screen %s", n_in, backlight[0] ? backlight : "none", screen_on ? "on" : "off");
    unsigned long long battery_at = now_ms(), scanned_at = now_ms();
    while (!stopping) {
        struct pollfd p[1 + INPUTS + CLIENTS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = api_fd, .events = POLLIN};
        for (int i = 0; i < n_in; ++i) p[n++] = (struct pollfd){.fd = in_fd[i], .events = POLLIN};
        for (int i = 0; i < CLIENTS; ++i) p[n++] = (struct pollfd){.fd = cl[i].fd, .events = POLLIN};
        unsigned long long t = now_ms();
        long long wait = (long long)(battery_at + BATTERY_S * 1000ull) - (long long)t;
        if (pressed_at) {
            long long hold = (long long)(pressed_at + HOLD_MS) - (long long)t;
            if (hold < wait) wait = hold;
        }
        if (!n_in && (long long)(scanned_at + RESCAN_MS) - (long long)t < wait) wait = (long long)(scanned_at + RESCAN_MS) - (long long)t;
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
        t = now_ms();
        if (pressed_at && t >= pressed_at + HOLD_MS) power_off();
        if (t >= battery_at + BATTERY_S * 1000ull) read_battery(), battery_at = t;
        if (!n_in && t >= scanned_at + RESCAN_MS) open_inputs(), scanned_at = t;
    }
    unlink(path("/run/vamp/sys.sock"));
    return 0;
}
