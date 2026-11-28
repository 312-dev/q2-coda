/* Vamp OS's services, spoken to directly: Vamp API 1 (docs/platform/README.md on feat/vamp-os-base), a line protocol
 * on a Unix socket per service in /run/vamp. Chosen at the start (--api auto|demo, api_open): with auto, the services
 * when their folder is there (a Vamp OS boot), else demo's pipe, queue and files as before (ui/player.c, ui/now.c).
 *
 * Q2PLAY/1 (q2playd, play.sock) is spoken here. Its hello, `caps` and `now` are read at the start with a short wait,
 * so the first page and verb already know what plays and which words the service has; then `watch` goes out, and every `now` line that
 * comes back is what plays (api_now_overlay, ui/now.c). A song's stars and like come from `rating 0`, asked when the
 * song changes and after a like. A verb whose first word caps does not name (today `queue` and `mode`) still goes
 * down q2playd's FIFO (ui/player.c player_send), which q2playd serves on Vamp OS as demo's shim does today; so does
 * the now file's shuffle and repeat while `now` has no such fields. A word the service gains later is used at once.
 *
 * Q2SYS/1 (the battery's cable and the jacks), Q2BT/1 and Q2NET/1 (their `status`) are watched for the status row's
 * and Control Center's states, which demo and q2gate write to /tmp/q2ui.status: api_told composes the same text from
 * them and config.ini, by q2gate's rules, and ui/status.c reads it in that file's place. Q2SYS/1's `screen` and its
 * `screen on`/`screen off` events say whether the screen is dark (api_screen_dark, for ui/ui.c ui_dark): the Q2 has
 * no backlight device to read, q2sysd lights it through stock's PWM. Centre is the power key (116 on md-gpio-keys):
 * q2sysd lights a dark screen at its release and powers off at a 3 s hold, and leaves a lit press to q2ui; any other
 * key pressed dark is sent on as `screen on`, and Now Playing's screen off as `screen off` (api_screen).
 *
 * A service that goes away (runsv restarts it) is connected to again at the next verb or the next change of the now
 * file, at most once a second. Everything runs in the loop's thread: the socket is ui_watch'd. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "q2ui.h"

const char *api_dir = "/run/vamp", *api_choice = "auto";
int api_vamp; /* 1: Vamp API 1's services, 0: demo's pipe and files */

typedef struct {
    const char *proto, *sock;
    int fd;
    char buf[2048];
    size_t len;
    char caps[512];  /* the caps line's words, each between spaces: " play toggle ... " */
    int outstanding; /* commands sent whose answer has not come (api_wait) */
    int now_wait;    /* of those, `now`s: the next now line answers one */
    uint64_t tried_us;
    void (*line)(char *line);
} service;

static void play_line(char *line), sys_line(char *line), bt_line(char *line), net_line(char *line);
static service play = { .proto = "Q2PLAY/1", .sock = "play.sock", .fd = -1, .line = play_line },
               sys = { .proto = "Q2SYS/1", .sock = "sys.sock", .fd = -1, .line = sys_line },
               bt = { .proto = "Q2BT/1", .sock = "bt.sock", .fd = -1, .line = bt_line },
               net = { .proto = "Q2NET/1", .sock = "net.sock", .fd = -1, .line = net_line };
static service *const services[] = { &play, &sys, &bt, &net };
#define SERVICES (int)(sizeof services / sizeof *services)

/* the status lines the system, Bluetooth and network services last said (their `battery`, `jack` and `status`) */
static char sys_battery[240], sys_jack[64], bt_status[600], net_status[400];
static int sys_lit = -1; /* Q2SYS/1's screen: 1 on, 0 off, -1 not said (no q2sysd, or one with no `screen`) */

/* what Q2PLAY/1 said plays: its own fields of ui_now, and whether a now line has come since the connection */
static struct {
    int live, rated;
    char title[256], artist[256], album[256];
    int state, pos, len, volume, id, shuffle, repeat, stars, liked;
    uint64_t at_us;
} pl;

static int hex(int c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'A' && c <= 'F' ? c - 'A' + 10 : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }
/* the value of key=... among line's space-separated fields, percent-decoded into out; 0 when absent */
int api_field(const char *line, const char *key, char *out, size_t room) {
    size_t k = strlen(key);
    for (const char *p = line; p && *p; p = strchr(p, ' '), p = p ? p + 1 : 0) {
        if (strncmp(p, key, k) || p[k] != '=') continue;
        size_t n = 0;
        for (const char *v = p + k + 1; *v && *v != ' ' && n + 1 < room; ++v) {
            if (*v == '%' && hex(v[1]) >= 0 && hex(v[2]) >= 0)
                out[n++] = (char)(hex(v[1]) << 4 | hex(v[2])), v += 2;
            else
                out[n++] = *v;
        }
        if (room) out[n] = 0;
        return 1;
    }
    return 0;
}
static int num(const char *line, const char *key, int dflt) {
    char v[24];
    return api_field(line, key, v, sizeof v) ? atoi(v) : dflt;
}

static void drop(service *s) {
    if (s->fd >= 0) ui_unwatch(s->fd), close(s->fd);
    s->fd = -1, s->len = 0, s->outstanding = s->now_wait = 0, *s->caps = 0;
    s->tried_us = mono_us(); /* not again within the second: now_read below asks for it */
    if (s == &play) pl.live = pl.rated = 0, now_read();
    if (s == &sys) *sys_battery = *sys_jack = 0, sys_lit = -1;
    if (s == &bt) *bt_status = 0;
    if (s == &net) *net_status = 0;
    if (s != &play) status_told_changed();
}
static int send_raw(service *s, const char *text, size_t n) {
    for (size_t at = 0; at < n;) {
        ssize_t w = send(s->fd, text + at, n - at, MSG_NOSIGNAL);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return drop(s), -1;
        at += (size_t)w;
    }
    return 0;
}
/* one command (its \n added), counted until its answer comes */
static int command(service *s, const char *cmd) {
    char line[520]; /* a client's line is at most 512 bytes */
    int n = snprintf(line, sizeof line, "%s\n", cmd);
    if (s->fd < 0 || n <= 0 || n > 512 || send_raw(s, line, (size_t)n)) return -1;
    ++s->outstanding;
    if (!strcmp(cmd, "now")) ++s->now_wait;
    return 0;
}
/* the lines that have come, each handed to the service's reader; 0 while the connection holds */
static int take(service *s) {
    for (;;) {
        ssize_t r = recv(s->fd, s->buf + s->len, sizeof s->buf - 1 - s->len, MSG_DONTWAIT);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return 0;
        if (r <= 0) return drop(s), -1;
        s->len += (size_t)r, s->buf[s->len] = 0;
        char *nl;
        while ((nl = memchr(s->buf, '\n', s->len))) {
            *nl = 0;
            char line[sizeof s->buf];
            memcpy(line, s->buf, (size_t)(nl - s->buf) + 1);
            s->len -= (size_t)(nl + 1 - s->buf);
            memmove(s->buf, nl + 1, s->len);
            int is_now = !strncmp(line, "now ", 4);
            /* an answer, not an event: a now line counts only for a `now` asked */
            if (s->outstanding && (!is_now || s->now_wait) && strncmp(line, "focus lost", 10))
                --s->outstanding, s->now_wait -= is_now;
            s->line(line);
        }
        if (s->len == sizeof s->buf - 1) s->len = 0; /* a line past any the protocol sends: dropped */
    }
}
static void play_ready(void) { take(&play); }
static void sys_ready(void) { take(&sys); }
static void bt_ready(void) { take(&bt); }
static void net_ready(void) { take(&net); }

/* connected, the hello, caps and the first state answered (within 500 ms), then `watch` asked; 0 done */
static int connect_to(service *s) {
    char path[300];
    struct sockaddr_un a = { .sun_family = AF_UNIX };
    uint64_t t = mono_us();
    if (s->fd >= 0) return 0;
    if (s->tried_us && t - s->tried_us < 1000000u) return -1;
    s->tried_us = t;
    snprintf(path, sizeof path, "%s/%s", api_dir, s->sock);
    if (strlen(path) >= sizeof a.sun_path) return -1;
    strcpy(a.sun_path, path);
    s->fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (s->fd < 0) return -1;
    if (connect(s->fd, (struct sockaddr *)&a, sizeof a)) return close(s->fd), s->fd = -1, -1;
    /* the hello, caps and the first state, all answered before the first page is drawn, so it starts as with demo */
    char hello[64];
    snprintf(hello, sizeof hello, "%s\ncaps\n%s", s->proto, s == &play ? "now\n" : s == &sys ? "battery\njack\nscreen\n" : "status\n");
    if (send_raw(s, hello, strlen(hello))) return -1;
    s->outstanding = s == &sys ? 5 : 3, s->now_wait = s == &play;
    for (uint64_t end = t + 500000u; s->fd >= 0 && s->outstanding && mono_us() < end;) {
        struct pollfd p = { s->fd, POLLIN, 0 };
        if (poll(&p, 1, (int)((end - mono_us()) / 1000u) + 1) > 0) take(s);
    }
    if (s->fd < 0 || !*s->caps) {
        fprintf(stderr, "q2ui: %s/%s did not answer %s\n", api_dir, s->sock, s->proto);
        return drop(s), -1;
    }
    ui_watch(s->fd, s == &play ? play_ready : s == &sys ? sys_ready : s == &bt ? bt_ready : net_ready);
    command(s, "watch");
    if (s != &play) status_told_changed();
    return 0;
}

/* the lines every service sends alike: the hello's answer, caps and an err; 1 when line was one */
static int common(service *s, const char *line) {
    char want[16];
    snprintf(want, sizeof want, "%s ok", s->proto);
    if (!strcmp(line, want)) return 1;
    if (!strncmp(line, "caps ", 5)) {
        snprintf(s->caps, sizeof s->caps, "%s ", line + 4); /* " play toggle ... " */
        return 1;
    }
    if (!strncmp(line, "err", 3)) return fprintf(stderr, "q2ui: %s said %s\n", s->proto, line), 1;
    return 0;
}
static void play_line(char *line) {
    if (common(&play, line)) return;
    if (!strncmp(line, "now ", 4)) {
        int was = pl.live ? pl.id : -1;
        api_field(line, "title", pl.title, sizeof pl.title) || (*pl.title = 0);
        api_field(line, "artist", pl.artist, sizeof pl.artist) || (*pl.artist = 0);
        api_field(line, "album", pl.album, sizeof pl.album) || (*pl.album = 0);
        pl.state = num(line, "state", 0), pl.pos = num(line, "pos", 0), pl.len = num(line, "len", 0);
        pl.volume = num(line, "volume", 0), pl.id = num(line, "id", 0);
        pl.shuffle = num(line, "shuffle", -2), pl.repeat = num(line, "repeat", -2); /* -2: not in the line */
        pl.at_us = mono_us(), pl.live = 1;
        if (pl.id != was) pl.rated = 0, command(&play, "rating 0");
        now_read();
        status_told_changed(); /* the volume Control Center shows */
        return;
    }
    if (!strncmp(line, "rating ", 7)) {
        if (num(line, "id", -1) == pl.id) pl.stars = num(line, "stars", 0), pl.liked = num(line, "liked", -1), pl.rated = 1;
        now_read();
    }
}

/* Q2SYS/1: the battery's line (an answer or a watch event alike), the jacks' (asked again at a plug or an unplug) and
 * the screen's (`screen`'s answer and the watch's events have the same words) */
static void sys_line(char *line) {
    if (common(&sys, line)) return;
    if (!strcmp(line, "screen on") || !strcmp(line, "screen off")) {
        sys_lit = line[8] == 'n';
        return;
    }
    if (!strncmp(line, "battery ", 8)) snprintf(sys_battery, sizeof sys_battery, "%s", line);
    else if (!strncmp(line, "jack po=", 8)) snprintf(sys_jack, sizeof sys_jack, "%s", line);
    else if (!strncmp(line, "jack ", 5)) command(&sys, "jack");
    else return;
    status_told_changed();
}
static void bt_line(char *line) {
    if (common(&bt, line)) return;
    if (!strncmp(line, "status ", 7)) snprintf(bt_status, sizeof bt_status, "%s", line), status_told_changed();
}
static void net_line(char *line) {
    if (common(&net, line)) return;
    if (!strncmp(line, "status ", 7)) snprintf(net_status, sizeof net_status, "%s", line), status_told_changed();
}

/* The status row's and Control Center's states (ui/status.c read_told), in the file's form demo and q2gate write
 * (docs/gate.md, The files): from the services' last lines and config.ini, by q2gate's rules (gate/q2gate.c tick,
 * on feat/vamp-os-base). A slot no service knows yet says what q2gate says: none or 0. 1 when composed. */
int api_told(char *out, size_t room) {
    static const char *const picks[] = { "Automatic", "Charge Only", "Transfer Files", "USB Audio", "USB Debugging", "Ask Every Time" };
    char state[24] = "", kind[24] = "", output[24] = "", netstate[24] = "";
    if (!api_vamp) return 0;
    for (int i = 1; i < SERVICES; ++i)
        if (services[i]->fd < 0) connect_to(services[i]);
    int jack = num(sys_jack, "po", 0) == 1 || num(sys_jack, "bal", 0) == 1;
    api_field(bt_status, "output", output, sizeof output);
    int wired = jack && strcmp(output, "bluetooth"), bton = num(bt_status, "power", 0) == 1;
    api_field(bt_status, "state", state, sizeof state), api_field(bt_status, "kind", kind, sizeof kind);
    /* the Bluetooth word by demo's rule (patch/status_row.c bt_slot) and q2gate's: nothing while off, on and idle, or
     * the wire takes the sound; a connected car its glyph, any other connected device the rune */
    const char *slot = !bton || wired                  ? "none"
                       : !strcmp(state, "connecting")  ? "connecting"
                       : strcmp(state, "connected")    ? "none"
                       : !strcmp(kind, "car")          ? "car"
                                                       : "rune";
    api_field(net_status, "state", netstate, sizeof netstate);
    int pick = config_int("Q2POD", "USBPICK", 0);
    snprintf(out, room,
             "out=%s\nbt=%s\nbtbatt=%d\nrelay=none\nlock=0\nsync=0\nsyncpm=0\noffline=0\nnet=1\nclock24=%d\n"
             "wifi=%d\nbton=%d\nbtbusy=0\nairplay=0\ncar=%d\nsmart=%d\neq=0\nusb=%s\ncable=%d\nbright=%d\nvol=%d\nvolmax=%d\n",
             wired ? "wire" : "other", slot, -1,
             config_int("SYSSET", "TIME24H", 1) != 0, *netstate && strcmp(netstate, "off"), bton,
             config_int("Q2POD", "CARREMOTE", 0) == 1, config_int("Q2POD", "SMARTWIFI", 0) == 1,
             picks[pick >= 0 && pick < 6 ? pick : 0], num(sys_battery, "cable", 0) == 1, config_int("SYSSET", "LIGHTNESS", 50),
             pl.live ? pl.volume : 0, config_int("PLAYSET", "MAXVOLUME", 100));
    return 1;
}

/* whether Q2SYS/1 says the screen is dark: 1 dark, 0 lit, -1 no word from it (off Vamp OS, or not connected) */
int api_screen_dark(void) { return api_vamp && sys.fd >= 0 && sys_lit >= 0 ? !sys_lit : -1; }

/* the screen lit (on 1) or dark (on 0) by Q2SYS/1: a question's wake (ui/notice.c), Now Playing's screen off; 0 sent,
 * -1 off Vamp OS (demo's pipe is the caller's then) */
int api_screen(int on) { return api_sys_send(on ? "screen on" : "screen off"); }

/* a command to Q2SYS/1 (connected first if it is not); 0 sent */
int api_sys_send(const char *verb) {
    if (!api_vamp || (sys.fd < 0 && connect_to(&sys))) return -1;
    return command(&sys, verb);
}

void api_open(void) {
    struct stat st;
    api_vamp = !strcmp(api_choice, "vamp") || (!strcmp(api_choice, "auto") && !stat(api_dir, &st) && S_ISDIR(st.st_mode));
    for (int i = 0; api_vamp && i < SERVICES; ++i) connect_to(services[i]);
}

/* the word w among the playback service's caps: a verb it serves, so one to send it */
int api_play_has(const char *w) {
    char word[40];
    if (!api_vamp || (play.fd < 0 && connect_to(&play))) return 0;
    snprintf(word, sizeof word, " %s ", w);
    return strstr(play.caps, word) != 0;
}
/* a verb to Q2PLAY/1; 0 sent */
int api_play_send(const char *verb) {
    char head[40];
    snprintf(head, sizeof head, "%.*s", (int)strcspn(verb, " "), verb);
    if (!api_play_has(head)) return -1;
    if (!strcmp(head, "like")) { /* the heart's flip (page_now.c): Q2PLAY/1's like says which way */
        char line[40];
        snprintf(line, sizeof line, "like %d %s", pl.rated && pl.liked == 1 ? 0 : 1, verb + 5);
        return command(&play, line) || command(&play, "rating 0") ? -1 : 0;
    }
    return command(&play, verb);
}
/* a command whose N lines of song ids follow it (play, queue), sent as one write; 0 sent */
int api_play_list(const char *head, const int *ids, int n) {
    if (!api_play_has(strncmp(head, "play", 4) ? "queue" : "play")) return -1;
    size_t room = strlen(head) + 2 + (size_t)n * 12, len;
    char *text = malloc(room);
    if (!text) return -1;
    len = (size_t)sprintf(text, "%s\n", head);
    for (int i = 0; i < n; ++i) len += (size_t)sprintf(text + len, "%d\n", ids[i]);
    int bad = send_raw(&play, text, len);
    free(text);
    if (!bad) ++play.outstanding;
    return bad ? -1 : 0;
}

/* What plays as Q2PLAY/1 said it, over what the now file said (ui/now.c now_read): 1 when it said anything. The
 * position is the line's second, counted from when the line came. */
int api_now_overlay(ui_now *n) {
    if (api_vamp && play.fd < 0) connect_to(&play);
    if (!pl.live) return 0;
    snprintf(n->title, sizeof n->title, "%s", pl.title);
    snprintf(n->artist, sizeof n->artist, "%s", pl.artist);
    snprintf(n->album, sizeof n->album, "%s", pl.album);
    n->playing = pl.state == 1, n->paused = pl.state == 2;
    n->pos_ms = pl.pos * 1000, n->len_s = pl.len, n->volume = pl.volume, n->id = pl.id;
    if (pl.shuffle != -2) n->shuffle = pl.shuffle;
    if (pl.repeat != -2) n->repeat = pl.repeat;
    n->hearts = pl.rated ? pl.stars : 0, n->liked = pl.rated ? pl.liked : -1;
    n->read_us = pl.at_us;
    return 1;
}
int api_play_live(void) { return play.fd >= 0; }

/* A script's wait for the answers to everything sent (main.c's `api`): up to ms of real time */
void api_wait(unsigned ms) {
    for (uint64_t end = mono_us() + (uint64_t)ms * 1000u; mono_us() < end;) {
        int busy = 0;
        for (int i = 0; i < SERVICES; ++i) {
            service *s = services[i];
            if (s->fd < 0 || !s->outstanding) continue;
            struct pollfd p = { s->fd, POLLIN, 0 };
            busy = 1;
            if (poll(&p, 1, 5) > 0) take(s);
        }
        if (!busy) break;
    }
}
