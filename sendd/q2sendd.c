/* q2sendd: LocalSend's receive side on Vamp OS (docs/vamp-os-services.md#localsend). Today's q2send (patch/localsend.c
 * on patch/localsend_core.c, docs/localsend.md) is the protocol, run here as a child; q2sendd is what demo's side
 * (patch/localsend_ui.c) is for it today, through the same files in --run:
 *
 *   - an incoming request (q2send.ask) is asked of the owner as a Q2UI/1 card (docs/platform/notices.md):
 *     "Receive files?", "<alias> sends <n> files (<size>)", Accept and Decline. Only `answer ID 1` accepts
 *     (q2send.answer "<id> 1"); any other answer, no UI socket, a refused ask or the UI's connection ending is a
 *     decline at once, never a wait. A request q2send drops first (its sender gone, its 60 s up) withdraws the card.
 *   - a trusted sender (q2send's data/trusted) is not asked: a toast "Receiving from <alias>" instead, so a receive
 *     is never silent.
 *   - the card a computer's over USB (Q2SYS/1 `usb storage`, at the start and as watched) is q2send.hold, which
 *     refuses every request; any other mode takes it away.
 *   - the radio held over Q2NET/1 while it runs (`hold`, renewed before it lapses), as Smart Wi-Fi's hold for
 *     LocalSend's page is today.
 *
 * SIGTERM ends q2send, then q2sendd; q2send ending ends q2sendd with its status. Usage:
 *
 *     q2sendd [--sockets DIR] [--q2send PATH] [--run DIR] [q2send's other options...]
 *
 * --sockets is where ui.sock, net.sock and sys.sock are (/run/vamp); --run is q2send's own (/tmp), passed on. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define HOLD_S 60     /* Q2NET/1's hold, renewed every HOLD_S / 2 */
#define LOOK_MS 200   /* q2send's files looked at this often */
#define RETRY_MS 2000 /* Q2SYS/1's connection tried again this often while it is not there */

static char socks[256] = "/run/vamp", run_dir[256] = "/tmp";
static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("q2sendd: ", stderr), vfprintf(stderr, fmt, ap), fputc('\n', stderr);
    va_end(ap);
}

static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}

static char *run_path(const char *name) {
    static char b[2][320];
    static int i;
    i ^= 1;
    snprintf(b[i], sizeof b[i], "%s/q2send.%s", run_dir, name);
    return b[i];
}

static int read_text(const char *p, char *out, size_t room) {
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    ssize_t n = fd < 0 ? -1 : read(fd, out, room - 1);
    if (fd >= 0) close(fd);
    out[n > 0 ? n : 0] = 0;
    return n > 0 ? (int)n : -1;
}

/* written whole and renamed into place, as q2send's own files are */
static void write_text(const char *p, const char *text) {
    char tmp[340];
    snprintf(tmp, sizeof tmp, "%s.q2sendd", p);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
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

/* one line to a service and its answer, on a connection of its own (Q2NET/1's hold, a toast) */
static int ask_once(const char *name, const char *hello, const char *line) {
    struct conn c;
    char got[256];
    if (sock_open(&c, name, hello)) return -1;
    int ok = !sock_say(&c, "%s", line) && sock_line(&c, got, sizeof got, 2000) == 1 && sock_line(&c, got, sizeof got, 2000) == 1 &&
             !strcmp(got, "ok");
    sock_close(&c);
    return ok ? 0 : -1;
}

static void toast(const char *text) {
    char enc[300], line[320];
    encode(enc, sizeof enc, text);
    snprintf(line, sizeof line, "toast %s", enc);
    if (ask_once("ui.sock", "Q2UI/1", line)) say("toast not shown: %s", text);
}

/* ---- the owner's card (Q2UI/1 ask) ---- */

static struct conn ui = {.fd = -1};
static char asked_id[96];   /* q2send's request id the open card is for, "" with none */
static char done_id[96];    /* the last one answered, its ask file not yet gone */
static char card[32];       /* the card's Q2UI/1 id, recv-N */
static int cards;

static void answer(const char *id, int yes) {
    char text[128];
    snprintf(text, sizeof text, "%s %d\n", id, yes);
    snprintf(done_id, sizeof done_id, "%s", id);
    write_text(run_path("answer"), text);
    say("%s: %s", id, yes ? "accepted" : "declined");
}

static void size_text(char *out, size_t room, long long b) {
    if (b < 1000) snprintf(out, room, "%lld B", b);
    else if (b < 1000000) snprintf(out, room, "%.1f KB", b / 1e3);
    else if (b < 1000000000) snprintf(out, room, "%.1f MB", b / 1e6);
    else snprintf(out, room, "%.1f GB", b / 1e9);
}

/* q2send.ask is "id alias model type files bytes", tab-separated */
static void look_ask(void) {
    char raw[512], *f[6] = {0};
    int have = read_text(run_path("ask"), raw, sizeof raw) > 0, n = 0;
    if (have) {
        raw[strcspn(raw, "\n")] = 0;
        for (char *p = raw, *t; n < 6 && (t = strsep(&p, "\t"));) f[n++] = t;
    }
    if (asked_id[0] && (!have || n < 6 || strcmp(f[0], asked_id))) { /* q2send gave up on it first */
        sock_say(&ui, "withdraw %s", card);
        say("%s: withdrawn (%s)", asked_id, have ? "a newer request" : "the request ended");
        asked_id[0] = 0;
    }
    if (!have || n < 6 || asked_id[0] || !strcmp(f[0], done_id)) return;
    snprintf(asked_id, sizeof asked_id, "%s", f[0]);
    snprintf(card, sizeof card, "recv-%d", ++cards);
    int files = atoi(f[4]);
    char size[32], body[200], title[64], b_enc[400], who[80];
    size_text(size, sizeof size, atoll(f[5]));
    snprintf(who, sizeof who, "%s", f[1]);
    snprintf(body, sizeof body, "%s sends %d file%s (%s)", who, files, files == 1 ? "" : "s", size);
    encode(title, sizeof title, "Receive files?"), encode(b_enc, sizeof b_enc, body);
    if (ui.fd < 0) sock_open(&ui, "ui.sock", "Q2UI/1");
    if (sock_say(&ui, "ask %s %s %s Accept Decline", card, title, b_enc)) {
        say("no Q2UI/1 to ask on");
        answer(asked_id, 0), asked_id[0] = 0;
    }
}

/* Q2UI/1's lines: its greeting, ask's ok, and the answer later */
static void read_ui(void) {
    char line[256];
    int r;
    while ((r = sock_line(&ui, line, sizeof line, 0)) == 1) {
        char id[40], what[16];
        if (!strncmp(line, "err", 3) && asked_id[0]) say("Q2UI/1: %s", line), answer(asked_id, 0), asked_id[0] = 0;
        else if (sscanf(line, "answer %39s %15s", id, what) == 2 && asked_id[0] && !strcmp(id, card))
            answer(asked_id, !strcmp(what, "1")), asked_id[0] = 0;
    }
    if (r < 0 && asked_id[0]) say("Q2UI/1 went away"), answer(asked_id, 0), asked_id[0] = 0;
}

/* ---- q2send's state: a trusted sender's receive told ---- */

static char recv_phase[16];

static void look_state(void) {
    char raw[4096], *line, *p = raw;
    if (read_text(run_path("state"), raw, sizeof raw) <= 0) return;
    while ((line = strsep(&p, "\n")))
        if (!strncmp(line, "recv\t", 5)) { /* recv phase files_done files bytes_done bytes alias current trusted: f[0] to f[8] */
            char *f[10] = {0};
            int n = 0;
            for (char *q = line, *t; n < 10 && (t = strsep(&q, "\t"));) f[n++] = t;
            if (n < 2) return;
            int now_on = !strcmp(f[1], "run") || !strcmp(f[1], "done"), was_on = !strcmp(recv_phase, "run") || !strcmp(recv_phase, "done");
            if (now_on && !was_on && n >= 9 && !strcmp(f[8], "1")) { /* a short one may be done by the next look */
                char text[120];
                snprintf(text, sizeof text, "Receiving from %s", f[6]);
                toast(text);
            }
            snprintf(recv_phase, sizeof recv_phase, "%s", f[1]);
            return;
        }
}

/* ---- the card over USB (Q2SYS/1) ---- */

static struct conn sys = {.fd = -1};

static void usb_mode(const char *mode) {
    int held = !access(run_path("hold"), F_OK), hold = !strcmp(mode, "storage");
    if (hold && !held) write_text(run_path("hold"), "usb\n"), say("the card is a computer's: holding");
    else if (!hold && held) unlink(run_path("hold")), say("the card is back");
}

static void read_sys(void) {
    char line[256];
    int r;
    while ((r = sock_line(&sys, line, sizeof line, 0)) == 1)
        if (!strncmp(line, "usb ", 4)) usb_mode(line + 4);
}

static int sys_open(void) {
    if (sock_open(&sys, "sys.sock", "Q2SYS/1")) return -1;
    sock_say(&sys, "usb"), sock_say(&sys, "watch"); /* the mode now, then each change */
    return 0;
}

int main(int argc, char **argv) {
    const char *q2send = "/usr/bin/q2send";
    char *args[64];
    int na = 0;
    args[na++] = 0; /* q2send's path, below */
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--sockets") && i + 1 < argc) snprintf(socks, sizeof socks, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--q2send") && i + 1 < argc) q2send = argv[++i];
        else if (!strcmp(argv[i], "--run") && i + 1 < argc) snprintf(run_dir, sizeof run_dir, "%s", argv[++i]);
        else if (na < 60) args[na++] = argv[i];
    }
    args[0] = (char *)q2send, args[na++] = "--run", args[na++] = run_dir, args[na] = 0;
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa = {.sa_handler = on_term};
    sigaction(SIGTERM, &sa, 0), sigaction(SIGINT, &sa, 0);
    unlink(run_path("hold")), unlink(run_path("answer"));
    pid_t child = fork();
    if (child == 0) {
        execv(q2send, args);
        _exit(127);
    }
    if (child < 0) return say("cannot start %s", q2send), 1;
    say("q2send %d started", (int)child);
    unsigned long long held_at = 0, sys_at = 0;
    int status = 0;
    while (!stopping) {
        unsigned long long t = now_ms();
        if (t >= held_at) {
            char hold[24];
            snprintf(hold, sizeof hold, "hold %d", HOLD_S);
            if (ask_once("net.sock", "Q2NET/1", hold)) say("Q2NET/1 did not take the hold");
            held_at = t + HOLD_S * 1000 / 2;
        }
        if (sys.fd < 0 && t >= sys_at) sys_open(), sys_at = t + RETRY_MS;
        struct pollfd p[2] = {{.fd = ui.fd, .events = POLLIN}, {.fd = sys.fd, .events = POLLIN}};
        if (poll(p, 2, LOOK_MS) < 0 && errno != EINTR) break;
        if (ui.fd >= 0 || asked_id[0]) read_ui();
        if (sys.fd >= 0) read_sys();
        look_ask();
        look_state();
        pid_t w = waitpid(child, &status, WNOHANG);
        if (w == child) {
            say("q2send ended (%d)", WIFEXITED(status) ? WEXITSTATUS(status) : -1);
            return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
        }
    }
    kill(child, SIGTERM);
    for (int i = 0; i < 50 && waitpid(child, &status, WNOHANG) == 0; ++i) usleep(100000);
    if (waitpid(child, &status, WNOHANG) == 0) kill(child, SIGKILL), waitpid(child, &status, 0);
    unlink(run_path("hold"));
    say("ended");
    return 0;
}
