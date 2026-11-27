/* Q2UI/1, the notice socket (docs/ui-rewrite.md#notices; the protocol is Vamp OS's
 * docs/platform/notices.md): a service or an app puts a toast over the page, or asks the owner a
 * question on the confirmation card (docs/oled-design.md#confirmation-card) and is answered on the
 * same connection.
 *
 * Lines in the common shape (docs/platform/README.md#the-common-shape on Vamp OS): the client's
 * first line "Q2UI/1", answered "Q2UI/1 ok"; then a command a line, each answered; free text
 * percent-encoded. At most CLIENTS connections, a line at most LINE_MAX bytes.
 *
 *     caps                                  caps toast ask watch
 *     toast TEXT                            ok; the pill for TOAST_MS, queued behind one showing
 *     ask ID TITLE BODY C1 [C2 [C3]]        ok, or err busy with ASKS waiting; later "answer ID N"
 *                                           (the choice from 1), "answer ID 0" (Return, the screen
 *                                           going off, TIMEOUT_S), or "answer ID gone" (withdrawn)
 *     withdraw ID                           ok, then answer ID gone
 *     watch                                 ok, then "front PAGE" now and as the page in front changes
 *
 * One card at a time, in the order asked; a connection that ends takes its questions with it. A toast
 * sent while a question is on the card waits for it to go. The
 * card names who asked: the app whose package user the caller's uid is (the apps file q2pkgd
 * keeps, the app's own q2app name=), or "Vamp" for the system's own uids, so an app cannot pose as
 * the system. An ask while the screen is dark lights it (ui_wake: today down demo's pipe, "wake");
 * a toast never does.
 *
 * Everything here runs in the loop's one thread: the socket and its connections are ui_watch'd,
 * read without waiting, and a command changes state like an input does (the card's motion is
 * stepped by notice_step, drawn by notice_draw over the page and the status row). */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "q2ui.h"

#define CLIENTS 8
#define LINE_MAX 512
#define ASKS 4        /* questions waiting, the one on the card among them */
#define TOASTS 4      /* toasts waiting, the one showing among them */
#define TOAST_MS 2500
#define TIMEOUT_S 60
#define ID_MAX 32
#define TITLE_MAX 40
#define BODY_MAX 160
#define CHOICE_MAX 16
#define TOAST_MAX 160
#define UID_APPS 10000 /* a package's first uid (perm/q2perm.h Q2PERM_UID_MIN on Vamp OS) */

/* the card's geometry (docs/oled-design.md#confirmation-card) */
#define CARD_X 42
#define CARD_W 291
#define CARD_R 16
#define CARD_MID 168
#define CARD_TOP 44
#define CARD_FOOT 302
#define TEXT_X (CARD_X + 20)
#define TEXT_W (CARD_W - 40)
#define PAD_TOP 18
#define FROM_H 18 /* the asker's name: 13 px on a 16 px line, 2 px under it */
#define TITLE_H 24
#define BODY_GAP 4
#define BODY_LINE 19
#define BODY_LINES 4
#define ANSWER_GAP 14
#define ANSWER_H 32
#define PAD_FOOT 8
#define BAR_INSET 8
#define IN_MS 200
#define OUT_MS 120
/* the toast (docs/oled-design.md#dialogs-and-toasts) */
#define TOAST_H 32
#define TOAST_MID 252
#define TOAST_SIDE 16
#define TOAST_R 12
#define TOAST_RISE 10
#define TOAST_SINK 4
#define TOAST_OUT_MS 150

const char *notice_socket = "/run/vamp/ui.sock", *notice_apps = "/mnt/data/vamp/apps", *notice_card = "/mnt/mmc";
void (*notice_said)(const char *line);
void (*notice_wake)(void);
void (*notice_changed)(void);
static void changed(void) {
    if (notice_changed) notice_changed();
}

static struct client {
    int fd, hello, watch, drop; /* drop: the rest of a line too long to keep */
    unsigned gen;               /* which connection held this slot, so an answer never reaches the next */
    size_t n;
    char in[LINE_MAX + 1];
    char from[48];              /* who asked, as the card names it */
} cl[CLIENTS];
static int listen_fd = -1;
static unsigned gens;

typedef struct {
    int client; /* -1: a script's */
    unsigned gen, serial;
    char id[ID_MAX + 1], title[TITLE_MAX + 1], body[BODY_MAX + 1], choice[3][CHOICE_MAX + 1], from[48];
    int n;
} ask_t;
static ask_t asks[ASKS];
static int n_asks;
static unsigned serials;

static struct {
    ask_t a;        /* the question on the card, a copy: it stays drawn while it fades out */
    int on, leaving, sel, lines, lit_seen;
    int pressed; /* the key that went down while the card asked: only its release answers */
    int told;    /* the stats file has the card as it now stands (notice_changed once a frame has shown it) */
    unsigned changed_at; /* the frame count when it changed last (ui_stats.frames) */
    uint64_t t0, until; /* the fade's start; when it answers itself */
    char line[BODY_LINES][BODY_MAX + 1];
    run_t from, title, body[BODY_LINES], answer[3];
    int made;
} card;

static char toasts[TOASTS][TOAST_MAX + 1];
static int n_toasts;
static struct {
    int on, leaving;
    uint64_t t0, until;
    char text[TOAST_MAX + 1];
    run_t run;
    int made;
} toast;

static char front[48];
/* the page in front by the name its route gives it (songs, wifi, now), else its page's own (a list without one is "list") */
static void front_name(char *out, size_t room) {
    char route[400];
    int segs = 1;
    ui_route(route, sizeof route);
    for (const char *c = route; *c; ++c) segs += *c == '/';
    const char *last = strrchr(route, '/') ? strrchr(route, '/') + 1 : route;
    if (*route && segs == ui_depth())
        snprintf(out, room, "%.*s", (int)strcspn(last, ":"), last);
    else
        snprintf(out, room, "%s", ui_top_name());
}

/* ---- lines ---- */

static void say(int c, const char *fmt, ...);
#define REPLY(...)           \
    do {                     \
        say(__VA_ARGS__);    \
        return;              \
    } while (0)
static void say(int c, const char *fmt, ...) {
    char line[LINE_MAX + 2];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    if (c < 0) {
        if (notice_said) notice_said(line);
        return;
    }
    line[n++] = '\n';
    (void)!send(cl[c].fd, line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT); /* a client that reads nothing loses lines, never the loop */
}

/* free text, %XX undone into out (room bytes, its end included); -1 when it does not fit or a % is not two hex digits */
static int hex(int ch) {
    return ch >= '0' && ch <= '9' ? ch - '0' : ch >= 'A' && ch <= 'F' ? ch - 'A' + 10 : ch >= 'a' && ch <= 'f' ? ch - 'a' + 10 : -1;
}
static int decode(const char *s, char *out, size_t room) {
    size_t n = 0;
    for (; *s; ++s) {
        int ch = (unsigned char)*s;
        if (ch == '%') {
            int h = hex(s[1]), l = h < 0 ? -1 : hex(s[2]);
            if (l < 0) return -1;
            ch = h * 16 + l, s += 2;
        }
        if (n + 1 >= room) return -1;
        out[n++] = (char)ch;
    }
    out[n] = 0;
    return 0;
}
static int id_ok(const char *id) {
    size_t n = strlen(id);
    if (!n || n > ID_MAX) return 0;
    for (size_t i = 0; i < n; ++i)
        if (!((id[i] >= 'a' && id[i] <= 'z') || (id[i] >= '0' && id[i] <= '9') || id[i] == '-')) return 0;
    return 1;
}

/* ---- who asks ---- */

/* the name the card gives a caller of uid: its app's (the apps file's "<id> <uid> <permissions>" line, "-" removed, and
 * that app's q2app name=), else the system's for a uid below the packages' */
static void asker(long uid, char *out, size_t room) {
    char l[512], id[64], perms[400], path[300];
    long u;
    FILE *f = fopen(notice_apps, "r");
    snprintf(out, room, "%s", uid >= 0 && uid < UID_APPS ? "Vamp" : "Unknown app");
    while (f && fgets(l, sizeof l, f)) {
        if (sscanf(l, "%63s %ld %399s", id, &u, perms) != 3 || u != uid) continue;
        if (!strcmp(perms, "-")) break;
        snprintf(out, room, "%s", id);
        snprintf(path, sizeof path, "%s/.%s/q2app", notice_card, id);
        FILE *m = fopen(path, "r");
        while (m && fgets(l, sizeof l, m))
            if (!strncmp(l, "name=", 5)) {
                l[strcspn(l, "\r\n")] = 0;
                if (l[5]) snprintf(out, room, "%.47s", l + 5);
                break;
            }
        if (m) fclose(m);
        break;
    }
    if (f) fclose(f);
}

/* ---- the card ---- */

static float card_level(void) {
    if (!card.on) return 0;
    float ms = (float)(ui_time() - card.t0) / 1000.0f;
    if (card.leaving) return clampf(1 - ms / OUT_MS, 0, 1); /* linear, under Reduce Motion too */
    return ui_reduce_motion || !list_motion ? 1 : ease_cubic_out(clampf(ms / IN_MS, 0, 1));
}

/* the body cut at words into lines of the text column, four at most (the last cut with an ellipsis when more is left) */
static void wrap(void) {
    const char *s = card.a.body;
    card.lines = 0;
    while (*s && card.lines < BODY_LINES) {
        char *line = card.line[card.lines];
        size_t n = 0, fit = 0;
        while (*s == ' ') ++s;
        if (card.lines == BODY_LINES - 1) {
            snprintf(line, BODY_MAX + 1, "%s", s);
            ++card.lines;
            break;
        }
        for (;;) {
            size_t w = strcspn(s + n, " ");
            char trial[BODY_MAX + 1];
            snprintf(trial, sizeof trial, "%.*s", (int)(n + w), s);
            if (fit && text_width(FACE_REGULAR, TYPE(15), trial) > TEXT_W) break;
            fit = n + w;
            n = fit;
            if (!s[n]) break;
            ++n; /* the space */
        }
        snprintf(line, BODY_MAX + 1, "%.*s", (int)fit, s);
        s += fit;
        ++card.lines;
    }
}
static float card_h(void) {
    return PAD_TOP + FROM_H + TITLE_H + (card.lines ? BODY_GAP + card.lines * BODY_LINE : 0) + ANSWER_GAP +
           card.a.n * ANSWER_H + PAD_FOOT;
}
static float card_y(void) {
    float h = card_h(), y = CARD_MID - h / 2;
    if (y + h > CARD_FOOT) y = CARD_FOOT - h;
    return y < CARD_TOP ? CARD_TOP : y;
}

static void card_show(void) {
    card.a = asks[0];
    card.on = 1, card.leaving = 0, card.sel = 0, card.made = 0, card.lit_seen = 0, card.pressed = 0, card.told = 0;
    card.changed_at = ui_stats.frames;
    card.t0 = ui_time();
    card.until = card.t0 + (uint64_t)TIMEOUT_S * 1000000u;
    wrap();
    ui_mark_all(), ui_again();
    if (ui_dark() && notice_wake) notice_wake(); /* a question is seen: the screen lights as a wake would */
    changed();
}
static int ask_find(unsigned serial) {
    for (int i = 0; i < n_asks; ++i)
        if (asks[i].serial == serial) return i;
    return -1;
}
static void ask_drop(int i) {
    memmove(asks + i, asks + i + 1, (size_t)(n_asks - i - 1) * sizeof *asks);
    --n_asks;
}
/* the question i is over: told its answer (when its connection is still the one that asked), off the card */
static void ask_end(int i, const char *answer) {
    ask_t *a = &asks[i];
    if (answer && (a->client < 0 || (cl[a->client].fd >= 0 && cl[a->client].gen == a->gen)))
        say(a->client, "answer %s %s", a->id, answer);
    int shown = card.on && !card.leaving && card.a.serial == a->serial;
    ask_drop(i);
    if (shown) card.leaving = 1, card.t0 = ui_time(), ui_mark_all(), ui_again();
    changed();
}
static void card_answer(const char *answer) {
    int i = ask_find(card.a.serial);
    if (i >= 0) ask_end(i, answer);
}

/* ---- the toast ---- */

static rect_t toast_rect(void) {
    float w = toast.made ? toast.run.w : text_width(FACE_REGULAR, TYPE(16), toast.text);
    int pill = (int)(w + 2 * TOAST_SIDE + 0.999f);
    if (pill > SCREEN_W - 2 * TOAST_SIDE) pill = SCREEN_W - 2 * TOAST_SIDE;
    return (rect_t){ (SCREEN_W - pill) / 2 - 2, TOAST_MID - TOAST_H / 2 - 2, pill + 4, TOAST_H + TOAST_RISE + 4 };
}
static void toast_show(void) {
    snprintf(toast.text, sizeof toast.text, "%s", toasts[0]);
    memmove(toasts, toasts + 1, (size_t)(n_toasts - 1) * sizeof *toasts);
    --n_toasts;
    toast.on = 1, toast.leaving = 0, toast.made = 0, toast.t0 = ui_time();
    toast.until = toast.t0 + (uint64_t)TOAST_MS * 1000u;
    ui_mark(toast_rect()), ui_again();
    changed();
}
/* q2ui's own toast, as a connection's: queued behind one showing, dropped when the queue is full */
void notice_toast(const char *text) {
    if (n_toasts == TOASTS - (toast.on ? 1 : 0)) return;
    snprintf(toasts[n_toasts++], TOAST_MAX + 1, "%s", text);
    if (!toast.on && !card.on) toast_show();
    else changed();
}

/* ---- commands ---- */

static void command(int c, char *line) {
    char *w[8];
    int n = 0;
    for (char *t = strtok(line, " "); t && n < 8; t = strtok(0, " ")) w[n++] = t;
    if (!n) REPLY(c, "err empty");
    if (!strcmp(w[0], "caps") && n == 1) REPLY(c, "caps toast ask watch");
    if (!strcmp(w[0], "toast")) {
        char text[TOAST_MAX + 1];
        if (n != 2) REPLY(c, "err toast");
        if (decode(w[1], text, sizeof text)) REPLY(c, "err text");
        if (n_toasts == TOASTS - (toast.on ? 1 : 0)) REPLY(c, "err busy");
        snprintf(toasts[n_toasts++], TOAST_MAX + 1, "%s", text);
        if (!toast.on && !card.on) /* a toast waits while a question is on the card */
            toast_show();
        else
            changed();
        REPLY(c, "ok");
    }
    if (!strcmp(w[0], "ask")) {
        if (n < 5 || n > 7) REPLY(c, "err ask");
        if (!id_ok(w[1])) REPLY(c, "err id");
        for (int i = 0; i < n_asks; ++i)
            if (asks[i].client == c && (c < 0 || asks[i].gen == cl[c].gen) && !strcmp(asks[i].id, w[1])) REPLY(c, "err id");
        if (n_asks == ASKS) REPLY(c, "err busy");
        ask_t a = { .client = c, .gen = c < 0 ? 0 : cl[c].gen, .serial = ++serials, .n = n - 4 };
        snprintf(a.id, sizeof a.id, "%s", w[1]);
        snprintf(a.from, sizeof a.from, "%s", c < 0 ? "Vamp" : cl[c].from);
        if (decode(w[2], a.title, sizeof a.title) || decode(w[3], a.body, sizeof a.body)) REPLY(c, "err text");
        for (int k = 0; k < a.n; ++k)
            if (decode(w[4 + k], a.choice[k], sizeof a.choice[k])) REPLY(c, "err text");
        asks[n_asks++] = a;
        say(c, "ok");
        if (!card.on) card_show();
        return;
    }
    if (!strcmp(w[0], "withdraw")) {
        if (n != 2) REPLY(c, "err withdraw");
        for (int i = 0; i < n_asks; ++i)
            if (asks[i].client == c && (c < 0 || asks[i].gen == cl[c].gen) && !strcmp(asks[i].id, w[1])) {
                say(c, "ok");
                ask_end(i, "gone");
                return;
            }
        REPLY(c, "err id");
    }
    if (!strcmp(w[0], "watch") && n == 1) {
        if (c >= 0) cl[c].watch = 1;
        say(c, "ok");
        REPLY(c, "front %s", front);
    }
    say(c, "err %s", w[0]);
}

void notice_line(const char *text) {
    char line[LINE_MAX + 1];
    snprintf(line, sizeof line, "%s", text);
    line[strcspn(line, "\r\n")] = 0;
    command(-1, line);
}

static void client_end(int c) {
    ui_unwatch(cl[c].fd);
    close(cl[c].fd);
    cl[c].fd = -1;
    for (int i = n_asks - 1; i >= 0; --i) /* its questions go with it: no one is left to tell an answer */
        if (asks[i].client == c && asks[i].gen == cl[c].gen) ask_end(i, 0);
}
static void client_line(int c, char *line) {
    if (!cl[c].hello) {
        if (strcmp(line, "Q2UI/1")) {
            say(c, "err version");
            client_end(c);
            return;
        }
        cl[c].hello = 1;
        say(c, "Q2UI/1 ok");
        return;
    }
    command(c, line);
}
/* every connection with something to read: whole lines acted on, a part kept for the next read */
static void clients(void) {
    for (int c = 0; c < CLIENTS; ++c) {
        while (cl[c].fd >= 0) {
            ssize_t got = recv(cl[c].fd, cl[c].in + cl[c].n, LINE_MAX - cl[c].n, MSG_DONTWAIT);
            if (got == 0 || (got < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
                client_end(c);
                break;
            }
            if (got < 0) break;
            size_t end = cl[c].n + (size_t)got, from = 0;
            for (size_t i = 0; i < end && cl[c].fd >= 0; ++i) {
                if (cl[c].in[i] != '\n') continue;
                cl[c].in[i] = 0;
                if (i && cl[c].in[i - 1] == '\r') cl[c].in[i - 1] = 0;
                if (cl[c].drop)
                    cl[c].drop = 0;
                else
                    client_line(c, cl[c].in + from);
                from = i + 1;
            }
            if (cl[c].fd < 0) break;
            cl[c].n = end - from;
            if (from && cl[c].n) memmove(cl[c].in, cl[c].in + from, cl[c].n);
            if (cl[c].n == LINE_MAX) { /* longer than a line may be: refused, the rest read past */
                if (!cl[c].drop) say(c, "err line");
                cl[c].drop = 1, cl[c].n = 0;
            }
        }
    }
}
static void accept_one(void) {
    int fd = accept(listen_fd, 0, 0);
    if (fd < 0) return;
    int c = 0;
    while (c < CLIENTS && cl[c].fd >= 0) ++c;
    if (c == CLIENTS) { /* full: the ninth is closed, as every API 1 service does */
        close(fd);
        return;
    }
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    long uid = -1;
#ifdef SO_PEERCRED
    struct ucred cr;
    socklen_t len = sizeof cr;
    if (!getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &len) && len == sizeof cr) uid = (long)cr.uid;
#endif
    cl[c] = (struct client){ .fd = fd, .gen = ++gens };
    asker(uid, cl[c].from, sizeof cl[c].from);
    ui_watch(fd, clients);
}

int notice_open(void) {
    for (int c = 0; c < CLIENTS; ++c) cl[c].fd = -1;
    front_name(front, sizeof front);
    if (!notice_socket || !*notice_socket) return 0;
    struct sockaddr_un a = { .sun_family = AF_UNIX };
    if (strlen(notice_socket) >= sizeof a.sun_path) return -1;
    strcpy(a.sun_path, notice_socket);
    char dir[sizeof a.sun_path];
    snprintf(dir, sizeof dir, "%s", notice_socket);
    char *slash = strrchr(dir, '/');
    if (slash && slash != dir) *slash = 0, mkdir(dir, 0755); /* /run/vamp, when no service made it yet */
    unlink(notice_socket); /* the last q2ui's, gone with it */
    listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (listen_fd < 0 || bind(listen_fd, (struct sockaddr *)&a, sizeof a) || listen(listen_fd, CLIENTS)) {
        fprintf(stderr, "q2ui: %s: %s\n", notice_socket, strerror(errno));
        if (listen_fd >= 0) close(listen_fd);
        listen_fd = -1;
        return -1;
    }
    chmod(notice_socket, 0666); /* ponytail: the notices and ask permissions (Vamp OS's groups) are not checked yet */
    ui_watch(listen_fd, accept_one);
    return 0;
}
void notice_close(void) {
    if (listen_fd < 0) return;
    close(listen_fd);
    listen_fd = -1;
    unlink(notice_socket);
}

/* ---- the frame ---- */

int notice_event(const input_t *e) {
    if (!card.on || card.leaving) return 0;
    if (e->type == IN_TICK) {
        int to = card.sel + e->value;
        if (to >= 0 && to < card.a.n) card.sel = to, card.told = 0, card.changed_at = ui_stats.frames, ui_click(0), ui_mark_all();
    } else if (e->type == IN_KEY_DOWN)
        card.pressed = e->value;
    else if (e->type == IN_KEY_UP && e->value != card.pressed)
        return 0; /* pressed before the card came: the page's, as it began there; never an answer */
    else if (e->type == IN_KEY_UP && e->value == KEY_UI_CENTRE) {
        char n[4];
        snprintf(n, sizeof n, "%d", card.sel + 1);
        card_answer(n);
    } else if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN)
        card_answer("0");
    if (e->type == IN_KEY_UP) card.pressed = 0;
    return 1; /* everything else does nothing while the card asks: a held Return opens no Control Center */
}

void notice_step(void) {
    char top[48];
    front_name(top, sizeof top);
    if (strcmp(top, front)) {
        snprintf(front, sizeof front, "%s", top);
        for (int c = 0; c < CLIENTS; ++c)
            if (cl[c].fd >= 0 && cl[c].watch) say(c, "front %s", front);
    }
    uint64_t now = ui_time();
    if (card.on && !card.leaving) {
        int dark = ui_dark();
        if (!dark) card.lit_seen = 1;
        if (now >= card.until || (dark && card.lit_seen)) /* no answer is not yes: timed out, or the screen went off */
            card_answer("0");
        else if (card_level() < 1)
            ui_mark_all(), ui_again();
        else {
            if (!card.told && ui_stats.frames != card.changed_at) /* a frame has shown it: the stats name its page */
                changed(), card.told = 1;
            ui_wake_ms(1000); /* the screen's state and the timeout are looked at each second */
        }
    }
    if (card.on && card.leaving) {
        if (card_level() <= 0) {
            card.on = 0;
            ui_mark_all();
            if (n_asks)
                card_show();
            else if (n_toasts && !toast.on)
                toast_show();
        } else
            ui_mark_all(), ui_again();
    }
    if (toast.on) {
        if (!toast.leaving && now >= toast.until) toast.leaving = 1, toast.t0 = now, changed();
        float ms = (float)(now - toast.t0) / 1000.0f;
        if (toast.leaving && ms >= TOAST_OUT_MS) {
            toast.on = 0;
            ui_mark(toast_rect());
            if (n_toasts && !card.on) toast_show();
        } else if (toast.leaving || (ms < IN_MS && !ui_reduce_motion && list_motion))
            ui_mark(toast_rect()), ui_again();
        else
            ui_wake_ms((unsigned)((toast.until - now + 999) / 1000u));
    }
}

static void toast_draw(rect_t r) {
    if (!toast.on || !rect_meet(r, toast_rect(), 0)) return;
    float ms = (float)(ui_time() - toast.t0) / 1000.0f, a, dy;
    if (toast.leaving) {
        float t = ease_cubic_in(clampf(ms / TOAST_OUT_MS, 0, 1));
        a = 1 - t, dy = TOAST_SINK * t;
    } else if (ui_reduce_motion || !list_motion)
        a = 1, dy = 0;
    else {
        float t = ease_cubic_out(clampf(ms / IN_MS, 0, 1));
        a = t, dy = TOAST_RISE * (1 - t);
    }
    if (!toast.made || run_stale(&toast.run))
        run_make(&toast.run, FACE_REGULAR, TYPE(16), toast.text, SCREEN_W - 4 * TOAST_SIDE), toast.made = 1;
    float w = toast.run.w + 2 * TOAST_SIDE, x = (SCREEN_W - w) / 2, y = TOAST_MID - TOAST_H / 2 + dy;
    fill_round((rectf_t){ x, y, w, TOAST_H }, TOAST_R, 0, tok(TOK_RAISED), a, 0);
    run_draw(&toast.run, x + TOAST_SIDE, y + (TOAST_H - TYPE(16)) / 2.0f, tok(TOK_PRIMARY), (unsigned)(a * 256));
}

static void card_draw_in(rect_t r) {
    float a = card_level();
    if (a <= 0) return;
    shade_all(r, 255 - (unsigned)(127 * a + 0.5f)); /* the page and its status row under black at 50% */
    float y = card_y(), h = card_h();
    if (!rect_meet(r, (rect_t){ CARD_X, (int)y, CARD_W, (int)(h + 1) }, 0)) return;
    if (!card.made) {
        run_make(&card.from, FACE_REGULAR, TYPE(13), card.a.from, TEXT_W);
        run_make(&card.title, FACE_MEDIUM, TYPE(20), card.a.title, TEXT_W);
        for (int k = 0; k < card.lines; ++k) run_make(&card.body[k], FACE_REGULAR, TYPE(15), card.line[k], TEXT_W);
        for (int k = 0; k < card.a.n; ++k) run_make(&card.answer[k], FACE_REGULAR, TYPE(20), card.a.choice[k], TEXT_W);
        card.made = 1;
    }
    unsigned ink = (unsigned)(a * 256);
    fill_round((rectf_t){ CARD_X, y, CARD_W, h }, CARD_R, 0, ui_light ? 0xffffff : tok(TOK_RAISED), a, 0);
    float at = y + PAD_TOP;
    run_draw(&card.from, SCREEN_W / 2.0f - card.from.w / 2, at, tok(TOK_TERTIARY), ink);
    at += FROM_H;
    run_draw(&card.title, SCREEN_W / 2.0f - card.title.w / 2, at, tok(TOK_PRIMARY), ink);
    at += TITLE_H;
    if (card.lines) at += BODY_GAP;
    for (int k = 0; k < card.lines; ++k, at += BODY_LINE)
        run_draw(&card.body[k], SCREEN_W / 2.0f - card.body[k].w / 2, at, tok(TOK_SECONDARY), ink);
    at += ANSWER_GAP;
    for (int k = 0; k < card.a.n; ++k, at += ANSWER_H) {
        int lit = k == card.sel;
        if (lit) fill_round((rectf_t){ CARD_X + BAR_INSET, at, CARD_W - 2 * BAR_INSET, ANSWER_H }, 8, 0, RGB_ACCENT, a, 0);
        run_draw(&card.answer[k], SCREEN_W / 2.0f - card.answer[k].w / 2, at + (ANSWER_H - TYPE(20)) / 2.0f,
                 lit ? 0xffffff : tok(TOK_PRIMARY), ink);
    }
}

/* a toast showing as a question comes stays under the card's scrim; the next waits for the card to go */
void notice_draw(rect_t r) {
    clip_set(r);
    toast_draw(r);
    if (card.on) card_draw_in(r);
}

int notice_up(void) { return card.on && !card.leaving; }

/* the stats file's part: the question on the card (its id, who asked) and the toast up, JSON */
void notice_stats(char *out, size_t room) {
    char from[100];
    size_t n = 0;
    const char *f = card.on && !card.leaving ? card.a.from : "";
    for (; *f && n < sizeof from - 7; ++f)
        n += (unsigned char)*f < 0x20 || *f == '"' || *f == '\\' ? (size_t)sprintf(from + n, "\\u%04x", (unsigned char)*f)
                                                                : (size_t)sprintf(from + n, "%c", *f);
    from[n] = 0;
    snprintf(out, room, "\"card\": \"%s\", \"card_from\": \"%s\", \"toast\": %d, \"toasts_waiting\": %d",
             card.on && !card.leaving ? card.a.id : "", from, toast.on && !toast.leaving, n_toasts);
}

int notice_trace(char *out, size_t room) {
    if (!card.on && !toast.on) return 0;
    return snprintf(out, room, "notice card=%s lit=%d level=%.3f toast=%s waiting=%d", card.on ? card.a.id : "-", card.sel,
                    card_level(), toast.on ? (toast.leaving ? "leaving" : "on") : "-", n_toasts);
}
