/* q2gate: VampOS's side of q2ui's pipe and its three files, which demo's gate keeps on today's Vamp
 * (patch/q2ui_gate.c on feat/render-engine; docs/gate.md). q2ui takes settings and verbs down a pipe it is handed
 * (--ctl FD) and reads what demo says in three files; with no demo, q2gate reads the pipe (a FIFO the q2ui service
 * opens for it, /run/vamp/ui.ctl) and turns each line into Vamp API 1's words, and writes the files from what the
 * services answer, once a second and when a line changes something:
 *   /tmp/q2ui.status  the status row's states and Control Center's switches (status_row.c status_row_state,
 *                     control_center.c q2ui_cc_state), key=value a line
 *   /tmp/q2ui.wifi    "scan N", then "LEVEL LOCK STATE NAME" a network (wifi_flows.c wifi_flows_networks)
 *   /tmp/q2ui.bt      "scan N", then "PAIRED STATE MAC NAME" a device, the paired first (bt_ui.c bt_q2ui_devices)
 * Each is written whole beside itself and renamed, only when it changes. Usage:
 *   q2gate [--root DIR] */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define LINE 80          /* a pipe line, its '\n' included (q2ui_gate.c Q2UI_CTL_LINE); a longer one is dropped */
#define TICK_MS 1000     /* the services asked again this often */
#define JOIN_MS 30000    /* a join not connected by then: "Couldn't connect" */
#define ROOM 2048        /* each list file at most (q2ui_gate.c Q2UI_WIFI_ROOM) */
#define NETS 40

static char root[256];
/* rel under --root, in one of four buffers in turn: the main thread's alone */
static const char *path(const char *rel) {
    static char b[4][700];
    static int k;
    k = (k + 1) % 4;
    snprintf(b[k], sizeof b[k], "%s%s", root, rel);
    return b[k];
}
static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}
static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2gate: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* test/gate.py's race test builds with ASK_HOOK a wait between naming a service's socket and connecting to it, the
 * window in which another thread's path() used to overwrite the name */
#ifndef ASK_HOOK
#define ASK_HOOK()
#endif

/* ---- the services: one exchange each, its answer's lines into out (up to the "ok" of a list), 0; -1 when the
 * service is not there or does not greet ---- */
static int ask(const char *sock, const char *hello, const char *cmd, char *out, size_t room, int secs) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    if ((size_t)snprintf(a.sun_path, sizeof a.sun_path, "%.*s%s", (int)(sizeof a.sun_path - 1), root, sock) >= sizeof a.sun_path)
        return -1; /* not path(): its buffers are the main thread's, and ask runs on the scans' and links' threads too */
    ASK_HOOK();
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct timeval tv = {secs, 0};
    char req[300], b[4096];
    size_t n = 0;
    if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv), setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int len = snprintf(req, sizeof req, "%s\n%s\n", hello, cmd);
    if (len >= (int)sizeof req || connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, req, (size_t)len, MSG_NOSIGNAL) != len)
        return close(fd), -1;
    /* the greeting, then lines until a single answer (not a listing's line) or a listing's ok */
    for (;;) {
        ssize_t k = recv(fd, b + n, sizeof b - 1 - n, 0);
        if (k <= 0) break;
        n += (size_t)k, b[n] = 0;
        char *first = strchr(b, '\n');
        if (!first) continue;
        char *last = b + n - 1;
        if (*last != '\n') continue;
        char *tail = last; /* the last whole line's start */
        while (tail > first + 1 && tail[-1] != '\n') --tail;
        if (tail <= first) continue;
        if (!strncmp(tail, "ok\n", 3) || !strncmp(tail, "err", 3)) break;
        /* a single answer: a line that is not a listing's (ap, network, device) */
        if (strncmp(tail, "ap ", 3) && strncmp(tail, "network ", 8) && strncmp(tail, "device ", 7)) break;
    }
    close(fd);
    b[n] = 0;
    size_t hl = strlen(hello);
    if (strncmp(b, hello, hl) || strncmp(b + hl, " ok\n", 4)) return -1;
    snprintf(out, room, "%s", b + hl + 4);
    return 0;
}
static int ask_ok(const char *sock, const char *hello, const char *cmd, int secs) {
    char r[256];
    return ask(sock, hello, cmd, r, sizeof r, secs) || strncmp(r, "ok", 2) ? -1 : 0;
}
#define SYS "/run/vamp/sys.sock", "Q2SYS/1"
#define NET "/run/vamp/net.sock", "Q2NET/1"
#define BT "/run/vamp/bt.sock", "Q2BT/1"
#define PLAY "/run/vamp/play.sock", "Q2PLAY/1"

/* the value of key= in a line of fields (key=value, space apart; values percent-encoded), decoded */
static int field(const char *line, const char *key, char *out, size_t room) {
    size_t k = strlen(key);
    *out = 0;
    for (const char *p = line; (p = strstr(p, key));) {
        if ((p == line || p[-1] == ' ') && p[k] == '=') {
            size_t j = 0;
            for (p += k + 1; *p && *p != ' ' && *p != '\n' && j + 1 < room; ++p) {
                unsigned v;
                if (*p == '%' && sscanf(p + 1, "%2x", &v) == 1) out[j++] = (char)v, p += 2;
                else out[j++] = *p;
            }
            out[j] = 0;
            return 1;
        }
        p += k;
    }
    return 0;
}
static int field_int(const char *line, const char *key, int fallback) {
    char v[32];
    return field(line, key, v, sizeof v) && *v ? atoi(v) : fallback;
}
static void encode(char *out, size_t room, const char *in) { /* a free-text argument, percent-encoded */
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p && j + 4 < room; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = (char)*p;
    out[j] = 0;
}

/* ---- config.ini: one key read; one key written, the file rewritten with that line changed (or added) ---- */
static char *read_all(const char *p) {
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    char *b = 0;
    size_t n = 0, cap = 0;
    for (size_t k; (cap - n > 1 || (b = realloc(b, cap += 4096))) && (k = fread(b + n, 1, cap - n - 1, f)) > 0;) n += k;
    fclose(f);
    if (b) b[n] = 0;
    return b;
}
static int ini_get(const char *section, const char *key, int fallback) {
    char *t = read_all(path("/mnt/data/config.ini")), head[64];
    int v = fallback, in = 0;
    snprintf(head, sizeof head, "[%s]", section);
    for (char *l = t; l && *l;) {
        char *nl = strchr(l, '\n');
        size_t len = nl ? (size_t)(nl - l) : strlen(l);
        if (*l == '[') in = !strncmp(l, head, strlen(head));
        else if (in && !strncmp(l, key, strlen(key)) && l[strlen(key)] == '=') v = atoi(l + strlen(key) + 1);
        l += len + (nl != 0);
    }
    free(t);
    return v;
}
static int ini_set(const char *section, const char *key, int v) {
    char *t = read_all(path("/mnt/data/config.ini")), head[64], tmp[720];
    if (!t) t = calloc(1, 1);
    if (!t) return -1;
    snprintf(head, sizeof head, "[%s]", section);
    snprintf(tmp, sizeof tmp, "%s.q2gate", path("/mnt/data/config.ini"));
    FILE *f = fopen(tmp, "w");
    if (!f) return free(t), -1;
    int in = 0, done = 0, had = 0;
    for (char *l = t; *l;) {
        char *nl = strchr(l, '\n');
        size_t len = nl ? (size_t)(nl - l) : strlen(l);
        if (*l == '[') {
            if (in && !done) fprintf(f, "%s=%d\n", key, v), done = 1;
            in = !strncmp(l, head, strlen(head)), had |= in;
        }
        if (in && !done && !strncmp(l, key, strlen(key)) && l[strlen(key)] == '=') fprintf(f, "%s=%d\n", key, v), done = 1;
        else fwrite(l, 1, len, f), fputc('\n', f);
        l += len + (nl != 0);
    }
    if (!done && had) fprintf(f, "%s=%d\n", key, v); /* the section last in the file */
    else if (!done) fprintf(f, "%s\n%s=%d\n", head, key, v);
    free(t);
    if (fclose(f) || rename(tmp, path("/mnt/data/config.ini"))) return unlink(tmp), -1;
    return 0;
}

/* ---- the files ---- */
static void put(const char *rel, const char *text, char *last, size_t room) {
    if (!strcmp(text, last)) return;
    char tmp[720];
    snprintf(tmp, sizeof tmp, "%s.q2gate", path(rel));
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fputs(text, f);
    if (fclose(f) || rename(tmp, path(rel))) {
        unlink(tmp);
        return;
    }
    snprintf(last, room, "%s", text);
}

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static struct net { char ssid[33]; int level, lock, state; } nets[NETS];
static int n_nets, wifi_scanning;
static int bt_scanning;
static struct { char ssid[33]; unsigned long long at; int state; } join; /* the join q2ui asked for */
static int want_tick = 1;

static void escaped(char *out, size_t room, const char *s) { /* a name's \ and newline as \\ and \n */
    size_t j = 0;
    for (; *s && j + 3 < room; ++s) {
        if (*s == '\\' || *s == '\n') out[j++] = '\\';
        out[j++] = *s == '\n' ? 'n' : *s;
    }
    out[j] = 0;
}

/* ---- the pipe's verbs ---- */
static void *wifi_scan(void *unused) { /* Q2NET/1 scan: its ap lines, the levels as stock's steps */
    (void)unused;
    char *r = malloc(4096);
    if (r && !ask(NET, "scan", r, 4096, 15)) {
        struct net got[NETS];
        int n = 0;
        for (char *l = r; l && *l && n < NETS; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0) {
            if (strncmp(l, "ap ", 3)) continue;
            struct net *w = &got[n];
            if (!field(l, "ssid", w->ssid, sizeof w->ssid) || !*w->ssid) continue;
            int dbm = field_int(l, "signal", -100);
            w->level = dbm >= -65 ? 4 : dbm >= -75 ? 3 : dbm >= -80 ? 2 : 1; /* wifi_flows.c level_of */
            w->lock = field_int(l, "secure", 0) != 0, w->state = 0, ++n;
        }
        pthread_mutex_lock(&mu);
        memcpy(nets, got, sizeof got), n_nets = n;
        pthread_mutex_unlock(&mu);
    } else
        say("Wi-Fi's search: q2netd did not answer");
    free(r);
    pthread_mutex_lock(&mu);
    wifi_scanning = 0, want_tick = 1;
    pthread_mutex_unlock(&mu);
    return 0;
}
static void *bt_scan(void *unused) {
    (void)unused;
    if (ask_ok(BT, "scan", 5)) say("Bluetooth's scan: q2btd refused it");
    sleep(60); /* q2btd's discovery runs 60 s */
    pthread_mutex_lock(&mu);
    bt_scanning = 0, want_tick = 1;
    pthread_mutex_unlock(&mu);
    return 0;
}
static void *bt_link(void *arg) { /* stock's row click: the connected one disconnected, a paired one connected, a found one paired */
    char *mac = arg, cmd[40], *r = malloc(4096);
    int paired = 0, connected = 0;
    if (r && !ask(BT, "devices", r, 4096, 3))
        for (char *l = r; l && *l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0) {
            char a[18];
            if (!strncmp(l, "device ", 7) && field(l, "addr", a, sizeof a) && !strcasecmp(a, mac))
                paired = field_int(l, "paired", 0), connected = field_int(l, "connected", 0);
        }
    snprintf(cmd, sizeof cmd, "%s %s", connected ? "disconnect" : paired ? "connect" : "pair", mac);
    if (ask_ok(BT, cmd, 40)) say("Bluetooth: %s refused", cmd);
    free(r), free(mac);
    pthread_mutex_lock(&mu);
    want_tick = 1;
    pthread_mutex_unlock(&mu);
    return 0;
}
static void spawn(void *(*f)(void *), void *arg) {
    pthread_t t;
    if (pthread_create(&t, 0, f, arg)) return free(arg), (void)0;
    pthread_detach(t);
}

static int mac_ok(const char *m) {
    for (int i = 0; i < 17; ++i)
        if (i % 3 == 2 ? m[i] != ':' : !((m[i] >= '0' && m[i] <= '9') || ((m[i] | 32) >= 'a' && (m[i] | 32) <= 'f'))) return 0;
    return !m[17];
}

/* "join NAME": a saved network is q2netd's to join; an open one heard is saved (add) and joined; a secured one not
 * saved needs its password typed, which q2ui has no entry for yet (demo's kbwifipass_dialog): "Couldn't connect" */
static void wifi_join(const char *ssid) {
    char r[4096], enc[120], cmd[160];
    int saved = 0, lock = -1;
    if (!ask(NET, "networks", r, sizeof r, 3))
        for (char *l = r; l && *l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0) {
            char s[33];
            if (!strncmp(l, "network ", 8) && field(l, "ssid", s, sizeof s) && !strcmp(s, ssid)) saved = 1;
        }
    pthread_mutex_lock(&mu);
    for (int i = 0; i < n_nets; ++i)
        if (!strcmp(nets[i].ssid, ssid)) lock = nets[i].lock;
    snprintf(join.ssid, sizeof join.ssid, "%s", ssid), join.at = now_ms(), join.state = 1;
    pthread_mutex_unlock(&mu);
    encode(enc, sizeof enc, ssid);
    snprintf(cmd, sizeof cmd, "add %s", enc);
    if (!saved && lock == 0 && ask_ok(NET, cmd, 3)) say("Wi-Fi: the open network could not be saved");
    if (!saved && lock != 0) {
        say("Wi-Fi: a password for a new network is typed on a page q2ui does not have yet");
        pthread_mutex_lock(&mu);
        join.state = 3;
        pthread_mutex_unlock(&mu);
    }
    ask_ok(NET, "wake", 3);
}

/* "set NAME V": Control Center's and Settings' switches, as control_center.c q2ui_cc_set sets them */
static int set(const char *name, int v) {
    char cmd[40];
    if (!strcmp(name, "wifi")) return ask_ok(NET, v ? "wake" : "off", 3);
    if (!strcmp(name, "bt")) return ask_ok(BT, v ? "power on" : "power off", 10);
    if (!strcmp(name, "car")) return ini_set("Q2POD", "CARREMOTE", !!v); /* q2carrd follows it within 1 s */
    if (!strcmp(name, "smart")) return ini_set("Q2POD", "SMARTWIFI", !!v) | ask_ok(NET, v ? "smart on" : "smart off", 3);
    if (!strcmp(name, "light") && v >= 0 && v <= 2) return ini_set("IPOD", "LIGHT", v);
    if (!strcmp(name, "motion")) return ini_set("IPOD", "REDUCEMOTION", !!v);
    if (!strcmp(name, "home") && v >= 0 && v <= 1) return ini_set("IPOD", "HOME", v);
    if (!strcmp(name, "songartist") && v >= 0 && v <= 3) return ini_set("IPOD", "SONGARTIST", v);
    if (!strcmp(name, "songlength") && v >= 0 && v <= 2) return ini_set("IPOD", "SONGLENGTH", v);
    if (!strcmp(name, "night") && v >= 0 && v <= 2) return ini_set("Q2POD", "NIGHTSHIFT", v);
    if (!strcmp(name, "nightk") && v >= 2000 && v <= 5000 && !(v % 100)) return ini_set("Q2POD", "NIGHTK", v);
    if (!strcmp(name, "gapless")) return ini_set("PLAYSET", "GAPLESS", !!v); /* q2playd reads it again */
    if (!strcmp(name, "eq")) return ask_ok(PLAY, v ? "eq on" : "eq off", 3); /* q2playd's switch, EQFLAG too */
    if (!strcmp(name, "xfade")) return ini_set("Q2POD", "XFADE", !!v);
    if (!strcmp(name, "bright") && v >= 0 && v <= 100) {
        snprintf(cmd, sizeof cmd, "brightness %d", v);
        return ask_ok(SYS, cmd, 3) | ini_set("SYSSET", "LIGHTNESS", v);
    }
    return -1; /* eq: the EQ's state is q2player's file, not set from here yet */
}

/* Key Tone (clicker.c clicker_route, docs/gate.md#key-tone): Off ([SYSSET] KEYTONE not 1) nothing; [IPOD] CLICKER 0
 * Both, the click mixed into what q2player plays (Q2PLAY/1 click) and the buzzer (Q2SYS/1 click); 1 Speaker, the
 * buzzer alone and only while nothing listens (no jack plugged and the music on the DAC); 2 Headphones, the player's
 * alone. A press is a button's click, a turn the wheel's. */
static int listening; /* a jack plugged or another output: the tick's */
static void click(int button) {
    if (ini_get("SYSSET", "KEYTONE", 0) != 1) return;
    int mode = ini_get("IPOD", "CLICKER", 0);
    if (mode == 0 || mode == 2) ask_ok(PLAY, button ? "click button" : "click wheel", 1);
    if (mode == 0 || (mode == 1 && !listening)) ask_ok(SYS, "click", 1);
}

static void line(char *l) {
    char word[16], mac[18];
    int v;
    if (sscanf(l, "set %15s %d", word, &v) == 2) {
        if (set(word, v)) say("set %s %d refused", word, v);
    } else if (sscanf(l, "click %d", &v) == 1) {
        click(v != 0);
    } else if (!strcmp(l, "scan")) {
        pthread_mutex_lock(&mu);
        int start = !wifi_scanning;
        wifi_scanning = 1, want_tick = 1;
        pthread_mutex_unlock(&mu);
        if (start) spawn(wifi_scan, 0);
    } else if (!strncmp(l, "join ", 5)) {
        char ssid[33];
        size_t k = 0;
        for (const char *s = l + 5; *s && k < sizeof ssid - 1; ++s) ssid[k++] = *s == '\\' && s[1] ? (*++s == 'n' ? '\n' : *s) : *s;
        ssid[k] = 0;
        if (*ssid) wifi_join(ssid);
    } else if (!strcmp(l, "btscan")) {
        pthread_mutex_lock(&mu);
        int start = !bt_scanning;
        bt_scanning = 1, want_tick = 1;
        pthread_mutex_unlock(&mu);
        if (start) spawn(bt_scan, 0);
    } else if (sscanf(l, "btlink %17s", mac) == 1 && mac_ok(mac)) {
        spawn(bt_link, strdup(mac));
    } else if (sscanf(l, "btforget %17s", mac) == 1 && mac_ok(mac)) {
        char cmd[40];
        snprintf(cmd, sizeof cmd, "forget %s", mac);
        if (ask_ok(BT, cmd, 5)) say("Bluetooth: %s refused", cmd);
    } else if (!strcmp(l, "screenoff")) {
        ask_ok(SYS, "screen off", 3);
    } else if (!strcmp(l, "wake")) {
        ask_ok(SYS, "screen on", 3);
    } else if (sscanf(l, "playkey %d", &v) == 1) {
    } else
        return;
    pthread_mutex_lock(&mu);
    want_tick = 1;
    pthread_mutex_unlock(&mu);
}

/* ---- once a second: the services asked, the files written when they changed ---- */
static char last_status[2048], last_wifi[ROOM], last_bt[ROOM];
static void tick(void) {
    char sys_jack[160] = "", sys_bat[200] = "", net[400] = "", bt[600] = "", play[2800] = "", out[160] = "", *devl = malloc(8192);
    int have_net = !ask(NET, "status", net, sizeof net, 2), have_bt = !ask(BT, "status", bt, sizeof bt, 2);
    ask(SYS, "jack", sys_jack, sizeof sys_jack, 2), ask(SYS, "battery", sys_bat, sizeof sys_bat, 2);
    char eq[60] = "";
    ask(PLAY, "now", play, sizeof play, 2), ask(PLAY, "output", out, sizeof out, 2), ask(PLAY, "eq", eq, sizeof eq, 2);
    char state[24] = "", kind[24] = "", ssid[33] = "";
    int jack = field_int(sys_jack, "po", 0) == 1 || field_int(sys_jack, "bal", 0) == 1, wired = jack && !strncmp(out, "output dac", 10);
    listening = jack || (*out && strncmp(out, "output dac", 10));
    int bton = have_bt && field_int(bt, "power", 0) == 1;
    field(bt, "state", state, sizeof state), field(bt, "kind", kind, sizeof kind);
    const char *slot = !bton ? "none" : !strcmp(state, "connecting") ? "connecting"
                       : strcmp(state, "connected") ? "rune" : !strcmp(kind, "car") ? "car" : "headset";
    char netstate[24] = "";
    field(net, "state", netstate, sizeof netstate), field(net, "ssid", ssid, sizeof ssid);
    int wifi = have_net && strcmp(netstate, "off") && *netstate;
    static const char *const picks[] = {"Automatic", "Charge Only", "Transfer Files", "USB Audio", "USB Debugging", "Ask Every Time"};
    int pick = ini_get("Q2POD", "USBPICK", 0);
    char s[2048];
    snprintf(s, sizeof s,
             "out=%s\nbt=%s\nbtbatt=%d\nrelay=none\nlock=0\nsync=0\nsyncpm=0\noffline=0\nnet=1\nclock24=%d\n"
             "wifi=%d\nbton=%d\nbtbusy=0\nairplay=0\ncar=%d\nsmart=%d\neq=%d\nusb=%s\ncable=%d\nbright=%d\nvol=%d\nvolmax=%d\n",
             wired ? "wire" : "other", slot, !strcmp(slot, "headset") ? field_int(bt, "battery", -1) : -1,
             ini_get("SYSSET", "TIME24H", 1) != 0, wifi, bton, ini_get("Q2POD", "CARREMOTE", 0) == 1,
             ini_get("Q2POD", "SMARTWIFI", 0) == 1, field_int(eq, "on", 0) == 1, picks[pick >= 0 && pick < 6 ? pick : 0], field_int(sys_bat, "cable", 0) == 1,
             ini_get("SYSSET", "LIGHTNESS", 50), field_int(play, "volume", 0), ini_get("PLAYSET", "MAXVOLUME", 100));
    put("/tmp/q2ui.status", s, last_status, sizeof last_status);

    pthread_mutex_lock(&mu);
    if (join.state == 1 && wifi && !strcmp(ssid, join.ssid) && !strcmp(netstate, "connected")) join.state = 2;
    if (join.state == 1 && now_ms() >= join.at + JOIN_MS) join.state = 3;
    char w[ROOM], b[ROOM], e[80], one[160];
    size_t n = (size_t)snprintf(w, sizeof w, "scan %d\n", wifi_scanning);
    for (int pass = 0; wifi && pass < 2; ++pass)
        for (int i = 0; i < n_nets; ++i) {
            int st = !strcmp(nets[i].ssid, join.ssid) ? join.state : !strcmp(nets[i].ssid, ssid) && !strcmp(netstate, "connected") ? 2 : 0;
            if ((pass == 0) != (st != 0)) continue;
            escaped(e, sizeof e, nets[i].ssid);
            int k = snprintf(one, sizeof one, "%d %d %d %s\n", nets[i].level, nets[i].lock, st, e);
            if (n + (size_t)k >= sizeof w) break;
            memcpy(w + n, one, (size_t)k + 1), n += (size_t)k;
        }
    int scanning = bt_scanning;
    want_tick = 0;
    pthread_mutex_unlock(&mu);
    put("/tmp/q2ui.wifi", w, last_wifi, sizeof last_wifi);

    n = (size_t)snprintf(b, sizeof b, "scan %d\n", scanning);
    if (devl && bton && !ask(BT, "devices", devl, 8192, 2))
        for (int pass = 0; pass < 2; ++pass) /* the paired first, as stock lists them */
            for (char *l = devl; l && *l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0) {
                char mac[18], name[65], en[140];
                if (strncmp(l, "device ", 7) || !field(l, "addr", mac, sizeof mac) || !mac_ok(mac)) continue;
                int paired = field_int(l, "paired", 0) == 1;
                if ((pass == 0) != paired) continue;
                field(l, "name", name, sizeof name), escaped(en, sizeof en, name);
                int k = snprintf(one, sizeof one, "%d %d %s %s\n", paired, field_int(l, "connected", 0) == 1 ? 2 : 0, mac, en);
                if (k <= 0 || n + (size_t)k >= sizeof b) break;
                memcpy(b + n, one, (size_t)k + 1), n += (size_t)k;
            }
    free(devl);
    put("/tmp/q2ui.bt", b, last_bt, sizeof last_bt);
}

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2gate [--root DIR]\n"), 2;
    }
    signal(SIGPIPE, SIG_IGN), signal(SIGTERM, on_term), signal(SIGINT, on_term);
    mkdir(path("/run"), 0755), mkdir(path("/run/vamp"), 0755);
    if (mkfifo(path("/run/vamp/ui.ctl"), 0600) && errno != EEXIST) say("%s: %s", path("/run/vamp/ui.ctl"), strerror(errno));
    int ctl = open(path("/run/vamp/ui.ctl"), O_RDWR | O_NONBLOCK | O_CLOEXEC); /* RDWR: no EOF when q2ui goes */
    if (ctl < 0) return say("cannot open the pipe: %s", strerror(errno)), 1;
    char in[LINE + 1];
    size_t got = 0;
    unsigned long long ticked = 0;
    say("serving q2ui's pipe");
    while (!stopping) {
        struct pollfd p = {.fd = ctl, .events = POLLIN};
        unsigned long long t = now_ms();
        int wait = want_tick ? 0 : (int)(ticked + TICK_MS > t ? ticked + TICK_MS - t : 0);
        if (poll(&p, 1, wait) < 0 && errno != EINTR) break;
        if (p.revents & POLLIN) {
            ssize_t k = read(ctl, in + got, sizeof in - 1 - got);
            if (k > 0) got += (size_t)k;
            in[got] = 0;
            char *s = in;
            for (char *nl; (nl = strchr(s, '\n'));) *nl = 0, line(s), s = nl + 1;
            got -= (size_t)(s - in);
            memmove(in, s, got + 1);
            if (got == sizeof in - 1) got = 0; /* no line is this long: dropped */
        }
        t = now_ms();
        if (want_tick || t >= ticked + TICK_MS) tick(), ticked = t;
    }
    return 0;
}
