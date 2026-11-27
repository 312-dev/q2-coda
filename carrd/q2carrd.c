/* q2carrd: Car Remote's side on Vamp OS (docs/carrd.md). The coda-rc daemon (patch/coda_rc.c, docs/remote-protocol.md)
 * is the protocol, run here as a child; q2carrd is what demo's shim (patch/coda_rc_shim.c) is for it today, through
 * the same files in /tmp, less what q2playd already answers (the now and queue files, the command queue and its
 * acks: docs/playd.md):
 *
 *   - the switch: coda-rc runs while [Q2POD] CARREMOTE is 1 in config.ini (its default 0, coda_rc_shim.c
 *     coda_rc_enabled), looked at once a second (its time and size), and is stopped at once when it goes off; one that
 *     ends is started again at most every RESTART_MS (CR_RESTART_MS), with a pipe it tells down (--tell-fd) when it
 *     has written one of the files below, so they are read at once;
 *   - the pairing code: while /tmp/coda-rc.pair ("CODE\nNAME\n") is there, a Q2UI/1 card (docs/platform/notices.md)
 *     titled with the phone's name, the code and Cancel, as the shim's pair_prompt card; Cancel or Return writes
 *     /tmp/coda-rc.pair-cancel. Q2UI/1's own 60 s answer 0 is asked again while the file stands, so the card is up for
 *     the pairing's 120 s as stock's is; the card is withdrawn once the file goes, and /tmp/coda-rc.pair-end then
 *     toasted as the shim does ("NAME paired" for 1, "Pairing failed" for 0, nothing for -1);
 *   - a card app for a car's cover: the id in /tmp/coda-rc.wake started over Q2PKG/1 when its q2app says covers=1
 *     (addon.c addon_cover_wake);
 *   - the radio: Smart Wi-Fi's held over Q2NET/1 (hold, renewed before it lapses) while Car Remote is in use and Wi-Fi
 *     is connected, and NET_IDLE_MS after its last use (coda_rc_shim.c coda_rc_wants_wifi): its start, a phone online
 *     (/tmp/coda-rc.online not empty) and the pairing card up are each a use.
 *
 * SIGTERM stops coda-rc, then q2carrd. Usage:
 *
 *     q2carrd [--root DIR] [--sockets DIR] [--coda-rc PATH] [--ui-timeout MS] [coda-rc's other options...]
 *
 * --root prefixes q2carrd's paths (/tmp, /mnt: the tests'), and coda-rc's (it is passed on); --sockets is where ui.sock, net.sock and pkg.sock are
 * (/run/vamp); --ui-timeout is Q2UI/1's own timeout as q2carrd tells it from Return (UI_TIMEOUT_MS; the tests'
 * shorter). coda-rc is given --tell-fd N and --library vampos (q2libd's library: coda_rc_lib.c VAMPOS_DB). */
#define _GNU_SOURCE
#include <ctype.h>
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

#define LOOK_MS 1000      /* the switch and the files looked at this often, besides each tell */
#define RESTART_MS 30000  /* coda_rc_shim.c CR_RESTART_MS: a coda-rc that ended is started again at most this often */
#define NET_IDLE_MS 600000 /* coda_rc_shim.c CR_NET_IDLE_MS: the radio held this long after Car Remote's last use */
#define HOLD_S 60          /* Q2NET/1's hold, renewed every HOLD_S / 2 while wanted */
#define UI_RETRY_MS 2000   /* Q2UI/1 tried again this often while a card waits to be shown */
#define UI_TIMEOUT_MS 59000 /* an answer 0 this long after the ask is Q2UI/1's own timeout (notice.c TIMEOUT_S 60) */
#define STOP_MS 5000       /* coda-rc's end after a TERM, waited for this long before a KILL */

static char root[256], socks[256] = "/run/vamp";
static unsigned ui_timeout_ms = UI_TIMEOUT_MS; /* --ui-timeout MS: the tests' */
static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("q2carrd: ", stderr), vfprintf(stderr, fmt, ap), fputc('\n', stderr);
    va_end(ap);
}

static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}

static char *path(const char *rel) { /* rel under --root, in one of four buffers */
    static char b[4][512];
    static int i;
    i = (i + 1) & 3;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static int read_text(const char *p, char *out, size_t room) {
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    ssize_t n = fd < 0 ? -1 : read(fd, out, room - 1);
    if (fd >= 0) close(fd);
    out[n > 0 ? n : 0] = 0;
    return n > 0 ? (int)n : -1;
}

/* written whole and renamed into place, as coda-rc reads its files */
static void write_text(const char *p, const char *text) {
    char tmp[540];
    snprintf(tmp, sizeof tmp, "%s.q2carrd", p);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return;
    int ok = write(fd, text, strlen(text)) == (ssize_t)strlen(text);
    close(fd);
    if (!ok || rename(tmp, p)) unlink(tmp);
}

/* Vamp API 1's free text (docs/platform/README.md#the-common-shape): bytes below 0x21, '%', '"', '=' and 0x7f as %XX */
static void encode(char *out, size_t room, const char *s) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p && j + 4 < room; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = (char)*p;
    out[j] = 0;
}

/* ---- the sockets: a connection, its greeting, and its lines ---- */

struct conn { int fd; char in[1024]; size_t n; };

static int sock_open(struct conn *c, const char *name, const char *hello) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    c->n = 0, c->fd = -1;
    if (strlen(socks) + 1 + strlen(name) >= sizeof a.sun_path) return -1;
    memcpy(a.sun_path, socks, strlen(socks)), a.sun_path[strlen(socks)] = '/';
    memcpy(a.sun_path + strlen(socks) + 1, name, strlen(name) + 1);
    c->fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (c->fd < 0 || connect(c->fd, (struct sockaddr *)&a, sizeof a)) goto fail;
    char line[64];
    int n = snprintf(line, sizeof line, "%s\n", hello);
    if (write(c->fd, line, (size_t)n) != n) goto fail;
    return 0;
fail:
    if (c->fd >= 0) close(c->fd);
    c->fd = -1;
    return -1;
}

static void sock_close(struct conn *c) {
    if (c->fd >= 0) close(c->fd);
    c->fd = -1, c->n = 0;
}

static int sock_say(struct conn *c, const char *fmt, ...) {
    char line[600];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0 || n > (int)sizeof line - 2) return -1;
    line[n++] = '\n';
    if (c->fd < 0 || send(c->fd, line, (size_t)n, MSG_NOSIGNAL) != n) return sock_close(c), -1;
    return 0;
}

/* the next whole line read (its newline gone) into out: 1, 0 with none yet, -1 once the connection has ended */
static int sock_line(struct conn *c, char *out, size_t room, int wait_ms) {
    for (;;) {
        char *nl = memchr(c->in, '\n', c->n);
        if (nl) {
            size_t k = (size_t)(nl - c->in);
            snprintf(out, room, "%.*s", (int)k, c->in);
            memmove(c->in, nl + 1, c->n - k - 1), c->n -= k + 1;
            return 1;
        }
        if (c->fd < 0) return -1;
        struct pollfd p = {.fd = c->fd, .events = POLLIN};
        if (poll(&p, 1, wait_ms) <= 0) return 0;
        ssize_t r = read(c->fd, c->in + c->n, sizeof c->in - 1 - c->n);
        if (r <= 0) return sock_close(c), -1;
        c->n += (size_t)r;
        if (c->n >= sizeof c->in - 1) return sock_close(c), -1;
    }
}

/* one line to a service and its first answer line (got), on a connection of its own: 0 when it answered */
static int ask_once(const char *name, const char *hello, const char *line, char *got, size_t room) {
    struct conn c;
    char greet[64];
    if (sock_open(&c, name, hello)) return -1;
    int ok = !sock_say(&c, "%s", line) && sock_line(&c, greet, sizeof greet, 2000) == 1 && sock_line(&c, got, room, 2000) == 1;
    sock_close(&c);
    return ok ? 0 : -1;
}

static void toast(const char *text) {
    char enc[300], line[320], got[64];
    encode(enc, sizeof enc, text);
    snprintf(line, sizeof line, "toast %s", enc);
    if (ask_once("ui.sock", "Q2UI/1", line, got, sizeof got) || strcmp(got, "ok")) say("toast not shown: %s", text);
}

/* ---- the switch ---- */

/* [Q2POD] CARREMOTE from config.ini: 1 on, anything else (or none) off */
static int switch_read(void) {
    FILE *f = fopen(path("/mnt/data/config.ini"), "r");
    char line[256];
    int in = 0, on = 0;
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (line[0] == '[') in = !strcmp(line, "[Q2POD]");
        else if (in && !strncmp(line, "CARREMOTE=", 10)) on = atoi(line + 10) == 1;
    }
    if (f) fclose(f);
    return on;
}

static int switch_on(void) {
    static struct stat seen;
    static int on, looked;
    struct stat st;
    if (stat(path("/mnt/data/config.ini"), &st)) memset(&st, 0, sizeof st);
    if (!looked || st.st_size != seen.st_size || st.st_mtim.tv_sec != seen.st_mtim.tv_sec || st.st_mtim.tv_nsec != seen.st_mtim.tv_nsec)
        on = switch_read(), seen = st, looked = 1;
    return on;
}

/* ---- coda-rc ---- */

static pid_t child;
static int tell_fd = -1;
static unsigned long long started_at, use_at;

static void start(char **args, int na, const char *coda_rc) {
    int fds[2];
    char fd_arg[16];
    started_at = now_ms(), use_at = started_at;
    if (pipe2(fds, O_CLOEXEC)) fds[0] = fds[1] = -1;
    if (fds[1] >= 0) fcntl(fds[1], F_SETFD, 0); /* coda-rc's end, kept across its exec */
    snprintf(fd_arg, sizeof fd_arg, "%d", fds[1]);
    args[0] = (char *)coda_rc;
    int k = na;
    if (fds[1] >= 0) args[k++] = "--tell-fd", args[k++] = fd_arg;
    if (root[0]) args[k++] = "--root", args[k++] = root;
    args[k++] = "--library", args[k++] = "vampos", args[k] = 0;
    child = fork();
    if (child == 0) {
        execv(coda_rc, args);
        _exit(127);
    }
    if (fds[1] >= 0) close(fds[1]);
    if (child < 0) {
        say("cannot start %s", coda_rc), child = 0;
        if (fds[0] >= 0) close(fds[0]);
        return;
    }
    tell_fd = fds[0];
    say("coda-rc %d started", (int)child);
}

static void ended(int status) {
    say("coda-rc ended (%d)", WIFEXITED(status) ? WEXITSTATUS(status) : -1);
    child = 0;
    if (tell_fd >= 0) close(tell_fd), tell_fd = -1;
}

/* the files it leaves when killed past its own end (coda_rc.c main's end removes them itself) */
static void tidy(void) {
    static const char *const files[] = {"/tmp/coda-rc.cmd", "/tmp/coda-rc.pair", "/tmp/coda-rc.online", "/tmp/coda-rc.pid",
                                        "/tmp/coda-rc.ble", "/tmp/coda-rc.wake"};
    for (unsigned i = 0; i < sizeof files / sizeof *files; ++i) unlink(path(files[i]));
}

static void stop(void) {
    int status = 0;
    if (!child) return;
    kill(child, SIGTERM);
    unsigned long long end = now_ms() + STOP_MS;
    while (waitpid(child, &status, WNOHANG) == 0 && now_ms() < end) usleep(50000);
    if (waitpid(child, &status, WNOHANG) == 0) kill(child, SIGKILL), waitpid(child, &status, 0);
    ended(status);
    tidy();
}

/* ---- the pairing card (Q2UI/1 ask) ---- */

static struct conn ui = {.fd = -1};
static struct {
    int up;                  /* the card asked and not yet answered */
    char code[8], name[40];  /* the code it shows, and the phone asking (the toast after names it) */
    char id[24];             /* its Q2UI/1 id, pair-N */
    int cards;
    int done;                /* the code answered (Cancel): not asked again */
    unsigned long long asked_at, tried_at;
} pc;

static void withdraw(const char *why) {
    if (!pc.up) return;
    sock_say(&ui, "withdraw %s", pc.id);
    say("pairing card withdrawn (%s)", why);
    pc.up = 0;
}

static void ask_card(void) {
    char title[200], body[300], text[120];
    unsigned long long t = now_ms();
    if (ui.fd < 0 && t - pc.tried_at < UI_RETRY_MS && pc.tried_at) return;
    pc.tried_at = t;
    if (ui.fd < 0 && sock_open(&ui, "ui.sock", "Q2UI/1")) return; /* tried again while the code stands */
    snprintf(pc.id, sizeof pc.id, "pair-%d", ++pc.cards);
    snprintf(text, sizeof text, "%.3s %.3s. Enter this code in the Vamp app", pc.code, pc.code + 3);
    encode(title, sizeof title, pc.name), encode(body, sizeof body, text);
    if (sock_say(&ui, "ask %s %s %s Cancel", pc.id, title, body)) return;
    pc.up = 1, pc.asked_at = t;
    use_at = t;
    say("pairing card asked (%s)", pc.id);
}

/* /tmp/coda-rc.pair is "CODE\nNAME\n" (coda_rc.c crc_pair_show); its end's outcome in .pair-end (crc_pair_hide) */
static void look_pair(void) {
    char raw[96], code[8] = "";
    int have = read_text(path("/tmp/coda-rc.pair"), raw, sizeof raw) >= 7 && raw[6] == '\n';
    for (int i = 0; have && i < 6; ++i) have = isdigit((unsigned char)raw[i]);
    if (have) {
        memcpy(code, raw, 6), code[6] = 0;
        if (strcmp(code, pc.code)) { /* a new pairing: its own card */
            withdraw("a new code");
            char *name = raw + 7;
            name[strcspn(name, "\n")] = 0;
            snprintf(pc.code, sizeof pc.code, "%s", code);
            snprintf(pc.name, sizeof pc.name, "%.39s", *name ? name : "Phone");
            pc.done = 0;
        }
        if (!pc.up && !pc.done) ask_card();
        if (pc.up) use_at = now_ms();
    } else {
        withdraw("the pairing ended");
        pc.code[0] = 0, pc.done = 0;
    }
    if (read_text(path("/tmp/coda-rc.pair-end"), raw, sizeof raw) > 0) {
        unlink(path("/tmp/coda-rc.pair-end"));
        int outcome = atoi(raw);
        char text[64];
        snprintf(text, sizeof text, "%s paired", pc.name[0] ? pc.name : "Phone");
        if (outcome >= 0) toast(outcome ? text : "Pairing failed");
    }
}

/* Q2UI/1's lines: ask's ok, the answer later; Cancel (1), or Return (0) before Q2UI/1's own timeout, cancels */
static void read_ui(void) {
    char line[256];
    int r;
    while ((r = sock_line(&ui, line, sizeof line, 0)) == 1) {
        char id[40], what[16];
        if (!strncmp(line, "err", 3) && pc.up) say("Q2UI/1: %s", line), pc.up = 0;
        else if (sscanf(line, "answer %39s %15s", id, what) == 2 && pc.up && !strcmp(id, pc.id)) {
            pc.up = 0;
            if (!strcmp(what, "gone")) continue; /* withdrawn, or q2ui's end: asked again while the code stands */
            if (!strcmp(what, "0") && now_ms() - pc.asked_at >= ui_timeout_ms) {
                say("pairing card timed out: asked again");
                continue;
            }
            write_text(path("/tmp/coda-rc.pair-cancel"), "1\n");
            pc.done = 1;
            say("pairing cancelled");
        }
    }
    if (r < 0) pc.up = 0; /* q2ui went away: asked again once it is back */
}

/* ---- a card app for a cover, the phones online, the radio ---- */

static void look_wake(void) {
    char raw[48], manifest[160], line[128];
    if (read_text(path("/tmp/coda-rc.wake"), raw, sizeof raw) <= 0) return;
    unlink(path("/tmp/coda-rc.wake"));
    raw[strcspn(raw, "\n")] = 0;
    int ok = raw[0] != 0, covers = 0;
    for (const char *c = raw; *c; ++c) ok &= islower((unsigned char)*c) || isdigit((unsigned char)*c) || *c == '-' || *c == '_';
    if (!ok) return;
    snprintf(manifest, sizeof manifest, "/mnt/mmc/.%s/q2app", raw);
    FILE *f = fopen(path(manifest), "r");
    while (f && fgets(line, sizeof line, f)) covers |= !strncmp(line, "covers=1", 8) && (line[8] == '\n' || !line[8]);
    if (f) fclose(f);
    if (!covers) return say("%s: no covers=1, not started", raw);
    char cmd[80], got[64];
    snprintf(cmd, sizeof cmd, "start %s", raw);
    if (ask_once("pkg.sock", "Q2PKG/1", cmd, got, sizeof got) || strcmp(got, "ok")) say("%s: not started (%s)", raw, got);
    else say("%s started for a cover", raw);
}

static void hold_radio(unsigned long long t) {
    static unsigned long long held_at;
    char got[256];
    if (t - use_at >= NET_IDLE_MS || t < held_at) return;
    held_at = t + HOLD_S * 1000 / 2;
    if (ask_once("net.sock", "Q2NET/1", "status", got, sizeof got) || !strstr(got, "state=connected")) return;
    char hold[24];
    snprintf(hold, sizeof hold, "hold %d", HOLD_S);
    if (ask_once("net.sock", "Q2NET/1", hold, got, sizeof got) || strcmp(got, "ok")) say("Q2NET/1 did not take the hold");
}

int main(int argc, char **argv) {
    const char *coda_rc = "/usr/bin/coda-rc";
    char *args[72];
    int na = 1; /* args[0]: coda-rc's path, at each start */
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--sockets") && i + 1 < argc) snprintf(socks, sizeof socks, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--coda-rc") && i + 1 < argc) coda_rc = argv[++i];
        else if (!strcmp(argv[i], "--ui-timeout") && i + 1 < argc) ui_timeout_ms = (unsigned)atoi(argv[++i]);
        else if (na < 64) args[na++] = argv[i];
    }
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa = {.sa_handler = on_term};
    sigaction(SIGTERM, &sa, 0), sigaction(SIGINT, &sa, 0);
    int was_on = -1;
    while (!stopping) {
        unsigned long long t = now_ms();
        int on = switch_on(), status;
        if (on != was_on) say("Car Remote %s", on ? "on" : "off"), was_on = on;
        if (child && waitpid(child, &status, WNOHANG) == child) ended(status);
        if (!on && child) stop(), withdraw("Car Remote off"), pc.code[0] = 0;
        if (on && !child && (!started_at || t - started_at >= RESTART_MS)) start(args, na, coda_rc);
        if (on) {
            char online[8];
            look_pair();
            look_wake();
            if (read_text(path("/tmp/coda-rc.online"), online, sizeof online) > 0) use_at = t;
            hold_radio(t);
        }
        struct pollfd p[2] = {{.fd = tell_fd, .events = POLLIN}, {.fd = ui.fd, .events = POLLIN}};
        if (poll(p, 2, LOOK_MS) < 0 && errno != EINTR) break;
        if (tell_fd >= 0 && p[0].revents) {
            char drain[64];
            if (read(tell_fd, drain, sizeof drain) <= 0) close(tell_fd), tell_fd = -1; /* its end: waited for above */
        }
        if (ui.fd >= 0 && p[1].revents) read_ui();
    }
    stop(), withdraw("q2carrd's end");
    say("ended");
    return 0;
}
