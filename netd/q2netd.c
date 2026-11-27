/* q2netd: Vamp OS's network service (docs/vamp-os.md#who-owns-what, docs/netd.md), its first slice: Wi-Fi.
 *
 *   - The radio: on, it brings wlan0 up and runs wpa_supplicant on the networks saved in /mnt/data/wpa_supplicant.conf
 *     (stock's file and format, so stock and today's Vamp read what is saved here), and udhcpc once it is associated;
 *     off, it stops both and takes wlan0 down, as stock's networkStop does.
 *   - Smart Wi-Fi's rule (docs/internals.md#smart-wi-fi): the radio goes off after IDLE_S with nothing holding it. A hold
 *     is Q2NET/1's `hold S` (S seconds), `wake`, and /tmp/q2remote.used renamed into place (the Wi-Fi adbd's mark of a
 *     computer let in, docs/adb.md#adb-over-wi-fi), which holds it REMOTE_HOLD_S; the first BOOT_HOLD_S after the start
 *     hold it too, so a Q2 just started is reachable. `smart off` keeps it on.
 *   - The saved networks: q2netd edits the conf itself (a network={} block added or removed, the rest of the file kept)
 *     and asks wpa_supplicant to read it again (RECONFIGURE), so no update_config line is needed in a file stock wrote.
 *
 * On a card boot (--card-boot: Vamp OS run in a chroot by today's Vamp, docs/vamp-os-base.md#card-boot) Wi-Fi is how the
 * developer reaches today's adbd outside the chroot, so it is never turned off: a wpa_supplicant already running
 * (today's Vamp's) is left exactly as it is, nothing started, stopped or restarted beside it (state "external"); with
 * none, the radio is brought up on the saved networks and kept up, Smart Wi-Fi and `off` refused.
 *
 * wpa_supplicant is spoken to over its control socket (/var/run/wpa_supplicant/wlan0: text commands, a datagram each).
 * Q2NET/1 on /run/vamp/net.sock (docs/netd.md#q2net1). --root prefixes paths; --wpa, --dhcp, --up and --down name the
 * commands, for test/netd.py. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define IDLE_S 300          /* Smart Wi-Fi: 5 idle minutes */
#define REMOTE_HOLD_S 1800  /* a computer let in over Wi-Fi holds the radio 30 minutes (REMOTE_HOLD_MS) */
#define BOOT_HOLD_S 1800    /* and the first 30 minutes after the start (REMOTE_BOOT_MS) */
#define STATUS_EVERY_MS 3000
#define RESPAWN_MS 10000 /* a wpa_supplicant that ended is started again at most this often (no wlan0: it ends at once) */
#define CLIENTS 8

static char root[256], wpa_cmd[1200], dhcp_cmd[512], up_cmd[256], down_cmd[256], ctrl_dir[256] = "/var/run/wpa_supplicant";
static const char *iface = "wlan0";
static int idle_s = IDLE_S, boot_hold_s = BOOT_HOLD_S, smart = 1, card_boot, external;

static char *path(const char *rel) {
    static char b[4][600];
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
    fprintf(stderr, "q2netd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* ---- children ---- */

static pid_t spawn(const char *cmd) {
    pid_t p = fork();
    if (p == 0) {
        setsid();
        execl("/bin/sh", "sh", "-c", cmd, (char *)0);
        _exit(127);
    }
    return p;
}
static void stop_child(pid_t *p) {
    if (*p <= 0) return;
    kill(-*p, SIGTERM), kill(*p, SIGTERM);
    for (int i = 0; i < 20 && waitpid(*p, 0, WNOHANG) == 0; ++i) usleep(50000);
    if (waitpid(*p, 0, WNOHANG) == 0) kill(-*p, SIGKILL), kill(*p, SIGKILL), waitpid(*p, 0, 0);
    *p = 0;
}
static int run_cmd(const char *cmd) {
    int r = system(cmd);
    return WIFEXITED(r) ? WEXITSTATUS(r) : -1;
}

/* ---- wpa_supplicant's control socket ---- */

static int ctrl = -1;
static char ctrl_local[600];

/* a socket's path into its address; -1 when it is longer than an address holds */
static int sun(struct sockaddr_un *a, const char *p) {
    size_t n = strlen(p);
    if (n >= sizeof a->sun_path) return -1;
    memcpy(a->sun_path, p, n + 1);
    return 0;
}

static void ctrl_close(void) {
    if (ctrl >= 0) close(ctrl), unlink(ctrl_local);
    ctrl = -1;
}
static int ctrl_open(void) {
    if (ctrl >= 0) return 0;
    struct sockaddr_un l = {.sun_family = AF_UNIX}, r = {.sun_family = AF_UNIX};
    char remote[600];
    snprintf(ctrl_local, sizeof ctrl_local, "%s/q2netd-%d", path("/tmp"), (int)getpid());
    snprintf(remote, sizeof remote, "%s%s/%s", root, ctrl_dir, iface);
    if (sun(&l, ctrl_local) || sun(&r, remote)) return -1;
    unlink(ctrl_local);
    ctrl = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (ctrl < 0 || bind(ctrl, (struct sockaddr *)&l, sizeof l) || connect(ctrl, (struct sockaddr *)&r, sizeof r)) {
        ctrl_close();
        return -1;
    }
    return 0;
}
/* cmd's answer in out (room bytes), "" with no answer within 2 s */
static int wpa(const char *cmd, char *out, size_t room) {
    out[0] = 0;
    if (ctrl_open() || send(ctrl, cmd, strlen(cmd), 0) < 0) return ctrl_close(), -1;
    struct pollfd p = {.fd = ctrl, .events = POLLIN};
    while (poll(&p, 1, 2000) > 0) {
        ssize_t n = recv(ctrl, out, room - 1, 0);
        if (n <= 0) break;
        out[n] = 0;
        if (out[0] != '<') return 0; /* an event ("<3>CTRL-EVENT..."): not the answer */
    }
    out[0] = 0;
    return -1;
}

/* ---- the radio ---- */

static pid_t wpa_pid, dhcp_pid;
static unsigned long long wpa_at;
static int radio_on;
static unsigned long long held_until, started_at;
static struct { char state[32], ssid[128], ip[48]; } st;
static unsigned st_seq;

static void tell(const char *fmt, ...);

static void radio(int on) {
    if (external || (card_boot && !on)) return; /* a card boot's Wi-Fi is never taken down */
    if (on == radio_on) return;
    if (on) {
        run_cmd(up_cmd);
        wpa_pid = spawn(wpa_cmd), wpa_at = now_ms();
        snprintf(st.state, sizeof st.state, "searching"), st.ssid[0] = st.ip[0] = 0, ++st_seq;
        say("radio on");
    } else {
        stop_child(&dhcp_pid), stop_child(&wpa_pid);
        ctrl_close();
        run_cmd(down_cmd);
        snprintf(st.state, sizeof st.state, "off"), st.ssid[0] = st.ip[0] = 0, ++st_seq;
        say("radio off");
    }
    radio_on = on;
    tell("radio %s", on ? "on" : "off");
}

static void hold(unsigned seconds) {
    unsigned long long until = now_ms() + seconds * 1000ull;
    if (until > held_until) held_until = until;
    radio(1);
}

static void field(char *out, size_t room, const char *text, const char *key) {
    const char *p = text;
    size_t k = strlen(key);
    out[0] = 0;
    while (p && *p) {
        if (!strncmp(p, key, k) && p[k] == '=') {
            snprintf(out, room, "%.*s", (int)strcspn(p + k + 1, "\n"), p + k + 1);
            return;
        }
        p = strchr(p, '\n');
        if (p) ++p;
    }
}

static void poll_status(void) {
    char a[4096], state[32], ssid[128], ip[48];
    if (!radio_on || wpa("STATUS", a, sizeof a)) return;
    field(state, sizeof state, a, "wpa_state"), field(ssid, sizeof ssid, a, "ssid"), field(ip, sizeof ip, a, "ip_address");
    int up = !strcmp(state, "COMPLETED");
    if (up && dhcp_pid <= 0) dhcp_pid = spawn(dhcp_cmd);
    if (!up && dhcp_pid > 0) stop_child(&dhcp_pid);
    const char *mine = up ? (*ip ? "connected" : "associated") : !strcmp(state, "SCANNING") ? "scanning" : "searching";
    if (strcmp(mine, st.state) || strcmp(ssid, st.ssid) || strcmp(ip, st.ip)) {
        snprintf(st.state, sizeof st.state, "%s", mine), snprintf(st.ssid, sizeof st.ssid, "%s", ssid);
        snprintf(st.ip, sizeof st.ip, "%s", ip), ++st_seq;
    }
}

/* ---- the saved networks (stock's wpa_supplicant.conf) ---- */

static char *read_all(const char *p, size_t *n) {
    FILE *f = fopen(p, "r");
    char *b = calloc(1, 262145);
    *n = f && b ? fread(b, 1, 262144, f) : 0;
    if (f) fclose(f);
    if (b) b[*n] = 0;
    return b;
}
static int write_all(const char *p, const char *text) {
    char tmp[620];
    snprintf(tmp, sizeof tmp, "%s.q2netd", p);
    FILE *f = fopen(tmp, "w");
    if (!f) return -1;
    fputs(text, f);
    if (fclose(f) || rename(tmp, p)) return -1;
    return 0;
}
/* A conf string's value as wpa_supplicant reads a quoted one: printable ASCII without '"' and '\\' (anything else
 * refused here, as the file has no escape for it); 0 if the text cannot be written so. */
static int quotable(const char *s, size_t max) {
    size_t n = strlen(s);
    if (!n || n > max) return 0;
    for (; *s; ++s)
        if ((unsigned char)*s < 0x20 || *s == '"' || *s == '\\' || (unsigned char)*s == 0x7f) return 0;
    return 1;
}
/* the conf's network blocks: each block's ssid in names (n of them), and where it starts and ends */
static int networks(const char *t, char names[][128], const char **from, const char **to, int max) {
    int n = 0;
    for (const char *p = t; n < max && (p = strstr(p, "network={"));) {
        const char *end = strstr(p, "\n}");
        end = end ? end + 2 : p + strlen(p);
        const char *s = strstr(p, "ssid=\"");
        names[n][0] = 0;
        if (s && s < end) snprintf(names[n], 128, "%.*s", (int)strcspn(s + 6, "\""), s + 6);
        if (from) from[n] = p, to[n] = *end == '\n' ? end + 1 : end;
        ++n, p = end;
    }
    return n;
}
static int add_network(const char *ssid, const char *psk) {
    if (!quotable(ssid, 32) || (*psk && (!quotable(psk, 63) || strlen(psk) < 8))) return -1;
    size_t n;
    char *t = read_all(path("/mnt/data/wpa_supplicant.conf"), &n), names[64][128], block[600];
    if (!t) return -1;
    int k = networks(t, names, 0, 0, 64);
    for (int i = 0; i < k; ++i)
        if (!strcmp(names[i], ssid)) { free(t); return -2; } /* saved already: forget it first */
    if (*psk) snprintf(block, sizeof block, "%snetwork={\n\tssid=\"%s\"\n\tpsk=\"%s\"\n}\n", n && t[n - 1] != '\n' ? "\n" : "", ssid, psk);
    else snprintf(block, sizeof block, "%snetwork={\n\tssid=\"%s\"\n\tkey_mgmt=NONE\n}\n", n && t[n - 1] != '\n' ? "\n" : "", ssid);
    char *out = malloc(n + strlen(block) + 1);
    sprintf(out, "%s%s", t, block);
    int r = write_all(path("/mnt/data/wpa_supplicant.conf"), out);
    free(t), free(out);
    char a[64];
    if (!r && radio_on) wpa("RECONFIGURE", a, sizeof a);
    return r;
}
static int forget_network(const char *ssid) {
    size_t n;
    char *t = read_all(path("/mnt/data/wpa_supplicant.conf"), &n), names[64][128];
    const char *from[64], *to[64];
    if (!t) return -1;
    int k = networks(t, names, from, to, 64), r = -1;
    for (int i = 0; i < k; ++i)
        if (!strcmp(names[i], ssid)) {
            char *out = malloc(n + 1);
            sprintf(out, "%.*s%s", (int)(from[i] - t), t, to[i]);
            r = write_all(path("/mnt/data/wpa_supplicant.conf"), out);
            free(out);
            break;
        }
    free(t);
    char a[64];
    if (!r && radio_on) wpa("RECONFIGURE", a, sizeof a);
    return r;
}

/* ---- Q2NET/1 ---- */

static struct client { int fd, hello, watch; char in[512]; size_t n; } cl[CLIENTS];
static int api = -1;

/* percent-encoding of a free-text value, as Q2PLAY/1's (docs/playd.md#q2play1) */
static void enc(char *out, size_t room, const char *in) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p && j + 4 < room; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = (char)*p;
    out[j] = 0;
}
static int hexv(char c) { return c >= '0' && c <= '9' ? c - '0' : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10 : -1; }
static void dec(char *s) {
    char *o = s;
    for (; *s; ++s)
        if (*s == '%' && hexv(s[1]) >= 0 && hexv(s[2]) >= 0) *o++ = (char)(hexv(s[1]) << 4 | hexv(s[2])), s += 2;
        else *o++ = *s;
    *o = 0;
}

static void client_say(struct client *c, const char *fmt, ...) {
    char line[1200];
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
    char line[1100];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    for (int i = 0; i < CLIENTS; ++i)
        if (cl[i].fd >= 0 && cl[i].watch) client_say(&cl[i], "%s", line);
}
static void status_line(char *out, size_t room) {
    char ssid[400];
    enc(ssid, sizeof ssid, st.ssid);
    long long held = held_until > now_ms() ? (long long)(held_until - now_ms()) / 1000 : 0;
    snprintf(out, room, "status state=%s ssid=%s ip=%s smart=%d held=%lld", external ? "external" : radio_on ? st.state : "off", ssid, st.ip,
             smart, held);
}

static void client_line(struct client *c, char *line) {
    char a[8192], b[400], e[400];
    unsigned s;
    if (!c->hello) {
        if (strcmp(line, "Q2NET/1")) { client_say(c, "err version"); close(c->fd), c->fd = -1; return; }
        c->hello = 1;
        client_say(c, "Q2NET/1 ok");
    } else if (!strcmp(line, "status")) {
        status_line(a, sizeof a);
        client_say(c, "%s", a);
    } else if (!strcmp(line, "wake")) {
        hold((unsigned)idle_s);
        client_say(c, "ok");
    } else if (sscanf(line, "hold %u", &s) == 1 && s <= 86400) {
        hold(s);
        client_say(c, "ok");
    } else if (!strcmp(line, "off") || !strcmp(line, "smart on")) {
        if (card_boot) { client_say(c, "err card boot"); return; } /* the developer's way in stays up */
        if (line[0] == 'o') held_until = 0, radio(0);
        else smart = 1;
        client_say(c, "ok");
    } else if (!strcmp(line, "smart off")) {
        smart = 0;
        client_say(c, "ok");
    } else if (!strcmp(line, "networks")) {
        size_t n;
        char *t = read_all(path("/mnt/data/wpa_supplicant.conf"), &n), names[64][128];
        int k = t ? networks(t, names, 0, 0, 64) : 0;
        for (int i = 0; i < k; ++i) enc(e, sizeof e, names[i]), client_say(c, "network ssid=%s", e);
        free(t);
        client_say(c, "ok");
    } else if (!strcmp(line, "scan")) {
        hold((unsigned)idle_s);
        if (wpa("SCAN", b, sizeof b) || wpa("SCAN_RESULTS", a, sizeof a)) { client_say(c, "err scan"); return; }
        /* bssid / frequency / signal level / flags / ssid, a tab between, after a header line */
        for (char *l = strchr(a, '\n'); l && *++l;) {
            char *nl = strchr(l, '\n');
            if (nl) *nl = 0;
            char *f[5] = {l};
            for (int i = 1; i < 5 && f[i - 1]; ++i) f[i] = strchr(f[i - 1], '\t') ? strchr(f[i - 1], '\t') + 1 : 0;
            if (f[4]) {
                for (int i = 0; i < 4; ++i) *strchr(f[i], '\t') = 0;
                enc(e, sizeof e, f[4]);
                client_say(c, "ap ssid=%s signal=%s secure=%d", e, f[2], strstr(f[3], "WPA") || strstr(f[3], "WEP") ? 1 : 0);
            }
            l = nl;
        }
        client_say(c, "ok");
    } else if (!strncmp(line, "add ", 4)) {
        char *sp = strchr(line + 4, ' ');
        if (sp) *sp = 0;
        snprintf(b, sizeof b, "%s", line + 4), dec(b);
        snprintf(e, sizeof e, "%s", sp ? sp + 1 : ""), dec(e);
        int r = add_network(b, e);
        client_say(c, r == 0 ? "ok" : r == -2 ? "err saved already" : "err network");
    } else if (!strncmp(line, "forget ", 7)) {
        snprintf(b, sizeof b, "%s", line + 7), dec(b);
        client_say(c, forget_network(b) ? "err no such network" : "ok");
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

/* Whether a process named name runs that is not one of q2netd's own (today's Vamp's, on a card boot) */
static int running_elsewhere(const char *name) {
    DIR *d = opendir(path("/proc"));
    int found = 0;
    for (struct dirent *e; d && !found && (e = readdir(d));) {
        pid_t pid = (pid_t)atoi(e->d_name);
        if (pid <= 0 || pid == wpa_pid || pid == dhcp_pid) continue;
        char p[600], comm[64] = "";
        snprintf(p, sizeof p, "%s/proc/%s/comm", root, e->d_name);
        FILE *f = fopen(p, "r");
        if (f && fgets(comm, sizeof comm, f)) comm[strcspn(comm, "\n")] = 0;
        if (f) fclose(f);
        found = !strcmp(comm, name);
    }
    if (d) closedir(d);
    return found;
}

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        const char *v = i + 1 < argc ? argv[i + 1] : 0;
        if (!strcmp(argv[i], "--root") && v) snprintf(root, sizeof root, "%s", v), ++i;
        else if (!strcmp(argv[i], "--wpa") && v) snprintf(wpa_cmd, sizeof wpa_cmd, "%s", v), ++i;
        else if (!strcmp(argv[i], "--dhcp") && v) snprintf(dhcp_cmd, sizeof dhcp_cmd, "%s", v), ++i;
        else if (!strcmp(argv[i], "--up") && v) snprintf(up_cmd, sizeof up_cmd, "%s", v), ++i;
        else if (!strcmp(argv[i], "--down") && v) snprintf(down_cmd, sizeof down_cmd, "%s", v), ++i;
        else if (!strcmp(argv[i], "--idle") && v) idle_s = atoi(v), ++i;
        else if (!strcmp(argv[i], "--boot-hold") && v) boot_hold_s = atoi(v), ++i;
        else if (!strcmp(argv[i], "--card-boot")) card_boot = 1;
        else return fprintf(stderr, "usage: q2netd [--root DIR] [--wpa CMD] [--dhcp CMD] [--up CMD] [--down CMD] [--idle S] [--boot-hold S]\n"), 2;
    }
    if (!*wpa_cmd)
        snprintf(wpa_cmd, sizeof wpa_cmd, "exec /usr/sbin/wpa_supplicant -Dnl80211 -i%s -c%s/mnt/data/wpa_supplicant.conf -O%s%s", iface,
                 root, root, ctrl_dir);
    if (!*dhcp_cmd) snprintf(dhcp_cmd, sizeof dhcp_cmd, "exec /sbin/udhcpc -f -i %s -S", iface);
    if (!*up_cmd) snprintf(up_cmd, sizeof up_cmd, "ifconfig %s up", iface);
    if (!*down_cmd) snprintf(down_cmd, sizeof down_cmd, "ifconfig %s down", iface);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    for (int i = 0; i < CLIENTS; ++i) cl[i].fd = -1;
    mkdir(path("/run"), 0755), mkdir(path("/run/vamp"), 0755);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    if (sun(&a, path("/run/vamp/net.sock"))) return say("--root too long"), 2;
    unlink(a.sun_path);
    api = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (api < 0 || bind(api, (struct sockaddr *)&a, sizeof a) || listen(api, CLIENTS)) say("%s: %s", a.sun_path, strerror(errno));
    chmod(a.sun_path, 0600);
    snprintf(st.state, sizeof st.state, "off");
    started_at = now_ms();
    struct stat sb;
    if (card_boot) {
        smart = 0;
        external = running_elsewhere("wpa_supplicant");
        say(external ? "card boot: Wi-Fi left to today's Vamp" : "card boot: Wi-Fi up and kept up");
        if (!external && !stat(path("/mnt/data/wpa_supplicant.conf"), &sb)) radio(1);
    } else if (!stat(path("/mnt/data/wpa_supplicant.conf"), &sb)) {
        hold((unsigned)boot_hold_s); /* networks saved: on for the boot hold */
    }
    struct timespec used = {0, 0};
    ino_t used_ino = 0;
    unsigned seen = st_seq;
    unsigned long long polled = 0;
    while (!stopping) {
        struct pollfd p[1 + CLIENTS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = api, .events = POLLIN};
        for (int i = 0; i < CLIENTS; ++i) p[n++] = (struct pollfd){.fd = cl[i].fd, .events = POLLIN};
        poll(p, (nfds_t)n, 1000);
        if (p[0].revents & POLLIN) {
            int fd = accept4(api, 0, 0, SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < CLIENTS && cl[i].fd >= 0) ++i;
            if (fd >= 0 && i == CLIENTS) close(fd);
            else if (fd >= 0) cl[i] = (struct client){.fd = fd};
        }
        for (int i = 0; i < CLIENTS; ++i)
            if (cl[i].fd >= 0 && p[1 + i].fd == cl[i].fd && p[1 + i].revents) client_read(&cl[i]);
        /* the Wi-Fi adbd's mark: a computer let in (a new inode renamed into place, or a newer time) */
        if (!external && !stat(path("/tmp/q2remote.used"), &sb) &&
            (sb.st_mtim.tv_sec != used.tv_sec || sb.st_mtim.tv_nsec != used.tv_nsec || sb.st_ino != used_ino)) {
            hold(REMOTE_HOLD_S);
            used = sb.st_mtim, used_ino = sb.st_ino;
        }
        unsigned long long t = now_ms();
        if (radio_on && t >= polled + STATUS_EVERY_MS) poll_status(), polled = t;
        if (radio_on && smart && t >= held_until && t >= started_at && held_until) radio(0);
        if (wpa_pid > 0 && waitpid(wpa_pid, 0, WNOHANG) == wpa_pid) wpa_pid = 0, say("wpa_supplicant ended");
        if (dhcp_pid > 0 && waitpid(dhcp_pid, 0, WNOHANG) == dhcp_pid) dhcp_pid = 0;
        if (radio_on && !external && wpa_pid == 0 && t >= wpa_at + RESPAWN_MS) wpa_pid = spawn(wpa_cmd), wpa_at = t; /* started again */
        if (st_seq != seen) {
            char s[1200];
            status_line(s, sizeof s);
            tell("%s", s);
            seen = st_seq;
        }
    }
    if (!card_boot) radio(0);
    else if (!external) stop_child(&dhcp_pid), stop_child(&wpa_pid); /* its own, at its end */
    unlink(path("/run/vamp/net.sock"));
    return 0;
}
