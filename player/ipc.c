/* The command socket: demo listens on /tmp/sock_play and this connects, as hciplayer does. Messages both ways
 * are `{name\argument}`, several to a packet at times. Each command and what stock answers: docs/player.md. */
#include "q2player.h"
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static int sock = -1;
static pthread_mutex_t send_mu = PTHREAD_MUTEX_INITIALIZER;

int ipc_connect(const char *path) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path);
    sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0 || connect(sock, (struct sockaddr *)&a, sizeof a) < 0) {
        fprintf(stderr, "q2player: %s: %s\n", path, strerror(errno));
        return -1;
    }
    return 0;
}

void ipc_send(const char *text) {
    pthread_mutex_lock(&send_mu);
    if (sock >= 0) send(sock, text, strlen(text), MSG_NOSIGNAL);
    pthread_mutex_unlock(&send_mu);
}

/* demo writes a `}` in a path as %0x7d, since `}` ends a message. */
static void unescape(char *s) {
    char *p;
    while ((p = strstr(s, "%0x7d"))) {
        *p = '}';
        memmove(p + 1, p + 5, strlen(p + 5) + 1);
    }
}

/* url|ss=N|cache|percent|gapless[|hw]|90. Fields missing from a short message stay 0, as stock's atoi of nothing. */
static void parse_song(const char *arg, struct song *s, int has_hw) {
    char buf[URL_MAX + 64], *f[8] = {0}, *p = buf;
    int n = 0;
    snprintf(buf, sizeof buf, "%s", arg);
    for (f[n++] = p; n < 8 && (p = strchr(p, '|')); f[n++] = p) *p++ = 0;
    memset(s, 0, sizeof *s);
    snprintf(s->url, sizeof s->url, "%.*s", (int)sizeof s->url - 1, f[0]);
    unescape(s->url);
    if (f[1] && strlen(f[1]) > 3) s->start = atoi(f[1] + 3);
    if (f[2]) s->cache_kb = atoi(f[2]);
    if (f[3]) s->cache_pct = atoi(f[3]);
    if (f[4]) s->gapless = atoi(f[4]);
    if (has_hw && f[5]) s->hw = atoi(f[5]);
}

static void reply(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void reply(const char *fmt, ...) {
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    ipc_send(b);
}

/* Each song demo hands over, and each stop, in the unified log: what the player was asked to play, beside what demo
 * showed (the device, 2026-11-18: a lost stop left one song playing under another's title). A URL's query is left
 * out, as it carries the app's token. */
static void log_command(const char *name, const char *arg) {
    char line[200];
    int n;
    if (!strcmp(name, "mcl-stop"))
        n = snprintf(line, sizeof line, "q2player: mcl-stop");
    else if (!strcmp(name, "set-songurl") || !strcmp(name, "set-nexturl") || !strcmp(name, "set-gapless"))
        n = snprintf(line, sizeof line, "q2player: %s %.*s", name, (int)strcspn(arg, strncmp(arg, "http", 4) ? "|" : "|?"), arg);
    else
        return;
    plog('I', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

static void command(const char *name, const char *arg) {
    struct ctl *c = &ctl;
    int wake = 0;
    log_command(name, arg);
    pthread_mutex_lock(&c->mu);
    if (!strcmp(name, "player-status")) {
        if (!strcmp(arg, "request")) reply(c->state == PLAYING ? "{player-status\\playing}" : "{player-status\\ready}");
    } else if (!strcmp(name, "media-info")) {
        if (!strcmp(arg, "get-music-info")) {
            if (c->has_info)
                reply("{media-info\\%d|%d|%d|%d|%s|%d}", c->info.bitrate, c->info.rate, c->info.length, c->position, c->info.codec, c->info.bits);
            else
                reply("{media-info\\no-playing-status}");
        }
    } else if (!strcmp(name, "get_cache_buffer")) {
        reply("{media-cache-buffer\\%d}", stream_cache_fill()); /* 0 for a file on the card, which has no cache */
    } else if (!strcmp(name, "mcl-getuvmeter")) {
        reply("{mcl-getuvmeter\\%d|%d}", c->uv[0], c->uv[1]);
    } else if (!strcmp(name, "mcl-getspectrum")) {
        int *s = c->spectrum;
        reply("{mcl-getspectrum\\%d|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d}", s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9], s[10], s[11]);
    } else if (!strcmp(name, "mcl-pcmstate")) {
        reply("{mcl-pcmstate\\0}");
    } else if (!strcmp(name, "set-songurl") || !strcmp(name, "set-nexturl")) {
        parse_song(arg, &c->play_song, 1);
        c->play = 1, c->stop = 0; /* a stop before this song is not this song's (engine.c engine_thread) */
        c->preload = c->close_preload = 0;
        if (name[4] == 'n') c->next = 1, c->cut = 1;
        wake = 1;
    } else if (!strcmp(name, "set-gapless")) {
        parse_song(arg, &c->preload_song, 0);
        c->preload = 1;
        wake = 1;
    } else if (!strcmp(name, "mcl-closegapless")) {
        c->preload = 0, c->close_preload = 1;
    } else if (!strcmp(name, "mcl-pause")) {
        if (c->state != IDLE) c->paused = 1, wake = 1;
    } else if (!strcmp(name, "mcl-play")) {
        if (c->state != IDLE) c->paused = 0, wake = 1;
    } else if (!strcmp(name, "mcl-seek")) {
        if (c->state != IDLE) c->seek = 1, c->seek_to = atoi(arg), wake = 1;
    } else if (!strcmp(name, "mcl-stop")) {
        c->stop = c->cut = 1, c->preload = 0, c->close_preload = 1, wake = 1;
    } else if (!strcmp(name, "mcl-orderstop")) {
        /* stock closes a PCM left open after the last song; this player has none open while idle */
    } else if (!strcmp(name, "mcl-setvol")) {
        c->volume = atoi(arg), c->volume_changed = 1;
    } else if (!strcmp(name, "mcl-softvolflag")) {
        c->softvol = atoi(arg);
    } else if (!strcmp(name, "mcl-setoutputway")) {
        c->outputway = atoi(arg);
    } else if (!strcmp(name, "mcl-dsdmode")) {
        c->dsdmode = atoi(arg);
    } else if (!strcmp(name, "mcl-replaygain")) {
        c->replaygain = atoi(arg);
    } else if (!strcmp(name, "mcl-playspeed")) {
        c->speed = atoi(arg);
    } else if (!strcmp(name, "mcl-scan")) {
        /* Q2 Muse's own: the payload's hold-to-scan (patch/seek.c), sent only while SCAN_FILE says this player plays */
        c->scan = atoi(arg), wake = 1;
    } else if (!strcmp(name, "mcl-setexplayinfo")) {
        if ((unsigned)atoi(arg) < 3) c->explay = atoi(arg);
    } else if (!strcmp(name, "mcl-mqadecodemode")) {
        c->mqa = atoi(arg);
    } else if (!strcmp(name, "mcl-seteq")) {
        /* stock's ten gains and flag; Q2 Muse's payload sends it after each change to the EQ state on disk
         * (patch/peq_ui.c peq_stock_eq), which is what plays (docs/player-plan.md#deviations) */
        const char *p = arg;
        for (int i = 0; i < 10 && p; i++, p = strchr(p, '|') ? strchr(p, '|') + 1 : NULL) c->eq[i] = strtof(p, NULL);
        c->eq_on = p ? atoi(p) : 0;
        c->eq_changed = 1;
    } else if (!strcmp(name, "mcl-click")) {
        /* the Clicker's click in the headphones (patch/clicker.c), or the plug-in chirp (patch/charging.c); a song must
         * be playing, as nothing else is open */
        int kind = !strcmp(arg, "wheel") ? CLICK_WHEEL : !strcmp(arg, "button") ? CLICK_BUTTON : !strcmp(arg, "beep") ? CLICK_BEEP
                   : !strcmp(arg, "charge") ? CLICK_CHARGE : 0;
        if (kind && c->state == PLAYING && !c->paused) c->click = kind, wake = 1;
    }
    if (wake) pthread_cond_broadcast(&c->wake);
    pthread_mutex_unlock(&c->mu);
}

/* Reads until demo closes the socket. A message split across two packets is put together; stock drops it. */
void *ipc_thread(void *unused) {
    static char buf[0x2000];
    size_t have = 0;
    (void)unused;
    for (;;) {
        ssize_t n = recv(sock, buf + have, sizeof buf - 1 - have, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) exit(0); /* demo is gone, and starts a new player when it comes back */
        have += n;
        buf[have] = 0;
        char *p = buf, *open, *close;
        while ((open = strchr(p, '{')) && (close = strchr(open, '}'))) {
            *close = 0;
            char *sep = strchr(open, '\\');
            if (sep) {
                *sep = 0;
                command(open + 1, sep + 1);
            }
            p = close + 1;
        }
        open = strchr(p, '{'); /* an unfinished message stays for the next packet */
        have = open ? strlen(open) : 0;
        if (have >= sizeof buf - 1) have = 0;
        if (have) memmove(buf, open, have);
    }
}
