/* Guest Play's side in demo (docs/internals.md#guest-play). The q2-guest daemon (patch/q2_guest.c)
 * decides everything; this file only:
 * - starts it (at Bluetooth on when Guest Play is on, and for a command when it is not running) and
 *   passes the UI's commands to its FIFO (guest_send), keeping a few while it starts;
 * - reads its state file for the UI (guest_seq, guest_field, guest_active), at most every
 *   GUEST_READ_MS, from headset_poll;
 * - stops the owner's music when a guest starts and restarts it when the guest leaves, as the
 *   settings say, then sends the owner's song to the car again;
 * - hands the car's buttons to the guest's phone while one plays (guest_key, from headset_command);
 * - keeps stock's receiver page shut while Guest Play is on (guest_a2dp, hooked at btrecvA2dpFunc);
 * - holds a receiver's session (Spotify Connect) until the owner allows it, and stops it when
 * refused (guest_net, docs/internals.md#guest-play-receivers). */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define GUEST_DAEMON "/usr/bin/q2-guest"
#define GUEST_STATE "/tmp/q2-guest.state"
#define GUEST_CMD "/tmp/q2-guest.cmd"
#define GUEST_PID "/tmp/q2-guest.pid"
#define GUEST_CONFIG "/mnt/data/q2-guest/config"
#define GUEST_READ_MS 250
#define GUEST_START_MS 3000 /* a daemon just started is not started again before this */
#define GUEST_LINE 208      /* a command, its newline and NUL (the contract's 200 bytes) */
#define GUEST_QUEUE 4       /* commands kept while the daemon starts */

extern int stock_btrecv_trampoline(int event, void *data, void *user);

static struct {
    char raw[2048];               /* the state file behind a '\n', as field() reads it */
    unsigned read_at, started_at; /* time_now_ms, as headset.c times its polls */
    unsigned seq;
    volatile int on; /* Guest Play is on: read by guest_a2dp on libbtctl's thread */
    int active, stopped, was_playing;
    unsigned netdeny_seq;
    char held[12]; /* the receiver held paused until allowed */
    int queued;
    char queue[GUEST_QUEUE][GUEST_LINE];
} gp __attribute__((section(".scratch")));

static int guest_running(void) {
    char raw[24], proc[24];
    if (!state_read(GUEST_PID, raw, sizeof raw)) return 0;
    snprintf(proc, sizeof proc, "/proc/%d", atoi(raw + 1));
    return atoi(raw + 1) > 1 && !access(proc, 0);
}

static void guest_start(void) {
    unsigned now = time_now_ms();
    if (gp.started_at && now - gp.started_at < GUEST_START_MS) return;
    gp.started_at = now;
    if (!guest_running()) system("( " GUEST_DAEMON " >/dev/null 2>&1 & )");
}

/* One line to the daemon's FIFO; 0 when nothing reads it (the daemon is not up). */
static int guest_write(const char *line) {
    char b[GUEST_LINE];
    int n = snprintf(b, sizeof b - 1, "%s", line);
    if (n < 0 || n > (int)sizeof b - 2) return 0;
    b[n++] = '\n';
    int fd = open(GUEST_CMD, 0x81); /* O_WRONLY | O_NONBLOCK, MIPS: no reader fails at once */
    if (fd < 0) return 0;
    int ok = write(fd, b, n) == n;
    close(fd);
    return ok;
}

int guest_send(const char *line) {
    if (!gp.queued && guest_write(line)) return 1;
    if (gp.queued < GUEST_QUEUE) snprintf(gp.queue[gp.queued++], GUEST_LINE, "%s", line);
    guest_start();
    return 0;
}

unsigned guest_seq(void) { return gp.seq; }
void guest_field(const char *key, char *out, unsigned n) { field(gp.raw, key, out, n); }
int guest_active(void) { return gp.active; }

/* At each Bluetooth on (headset_status_register): the daemon, when Guest Play is on. */
void guest_bt_on(void) {
    char raw[128];
    if (strstr((state_read(GUEST_CONFIG, raw, sizeof raw), raw), "\nenabled=1\n")) guest_start();
}

/* A receiver's session starting (kind spotify, airplay, dlna; id who, as far as it knows): 1 to
 * hold it paused, as Guest Play is on and q2-guest has not let this guest play; then the owner is
 * asked, and guest_poll releases it once allowed. 0 to play as without Guest Play. */
int guest_net(const char *kind, const char *id, const char *name) {
    char state[12], source[12], line[GUEST_LINE];
    if (!gp.on) return 0;
    field(gp.raw, "state", state, sizeof state);
    field(gp.raw, "source", source, sizeof source);
    if ((!strcmp(state, "playing") || !strcmp(state, "paused")) && !strcmp(source, kind)) return 0;
    if (strcmp(gp.held, kind)) { /* asked once a hold */
        snprintf(line, sizeof line, "net %s %s %s", kind, id, name);
        guest_send(line);
        snprintf(gp.held, sizeof gp.held, "%s", kind);
    }
    return 1;
}

/* What q2-guest decided about a held or playing receiver: released when its guest plays, stopped
 * when refused (netdeny_seq counts each). */
static void net_follow(const char *state) {
    char source[12], deny[12];
    field(gp.raw, "source", source, sizeof source);
    if (*gp.held && !strcmp(state, "playing") && !strcmp(source, gp.held)) {
        if (!strcmp(gp.held, "spotify")) spot_send_play(1);
        gp.held[0] = 0;
    }
    unsigned seq = (unsigned)number(gp.raw, "netdeny_seq");
    if (seq == gp.netdeny_seq) return;
    gp.netdeny_seq = seq;
    field(gp.raw, "netdeny", deny, sizeof deny);
    if (!strcmp(deny, "spotify")) spot_send_play(0);
    if (!strcmp(deny, gp.held)) gp.held[0] = 0;
}

/* A guest started: the owner's music stops. The local player is stopped with its place kept, as a
 * paused hciplayer may hold its PCM, which bluealsa-aplay then can't open; the rest pause. */
static void owner_stop(void) {
    gp.was_playing = media_playing();
    int status = mclGetPlayStatus();
    gp.stopped = media_owner() == 1 && (status == 2 || status == 3);
    if (gp.stopped)
        player_save_stop();
    else if (gp.was_playing)
        media_set_playing(0);
}

/* The guest left: the owner's music again, playing if it was and the setting is Resume My Music. */
static void owner_resume(void) {
    char leave[8];
    field(gp.raw, "leave", leave, sizeof leave);
    int resume = gp.was_playing && strcmp(leave, "stay");
    if (gp.stopped)
        player_save_start(!resume);
    else if (resume)
        media_set_playing(1);
    gp.stopped = gp.was_playing = 0;
    sendBtHeadsetPlayInfo(); /* the car shows the owner's song again, not the guest's last */
    sendBtHeadsetPlayStatus();
}

void guest_poll(void) {
    unsigned now = time_now_ms();
    if (now - gp.read_at < GUEST_READ_MS) return;
    gp.read_at = now;
    state_read(GUEST_STATE, gp.raw, sizeof gp.raw);
    gp.seq = (unsigned)number(gp.raw, "seq");
    char state[12];
    field(gp.raw, "state", state, sizeof state);
    gp.on = gp.seq && strcmp(state, "off");
    while (gp.queued && guest_write(gp.queue[0])) {
        --gp.queued;
        memmove(gp.queue[0], gp.queue[1], gp.queued * GUEST_LINE);
    }
    if (gp.queued) guest_start();
    if (gp.seq) net_follow(state);
    int active = !strcmp(state, "playing") || !strcmp(state, "paused");
    if (active == gp.active) return;
    gp.active = active;
    char source[12];
    field(gp.raw, "source", source, sizeof source);
    if (active && !*source)
        owner_stop();
    else if (!active)
        owner_resume(); /* after a receiver's guest: nothing was stopped, the car gets the owner's
                           song */
}

/* headset_command's codes while a guest plays: to the guest's phone. 1 when taken. */
int guest_key(int code) {
    if (!gp.active) return 0;
    const char *key = code == 163                                 ? "key next"
                      : code == 165                               ? "key prev"
                      : code == 164                               ? "key toggle"
                      : code == 119 || code == 201 || code == 166 ? "key pause"
                      : code == 200 || code == 207                ? "key play"
                                                                  : 0;
    if (key) guest_send(key);
    return key != 0;
}

/* btrecvA2dpFunc's hook (tools/build.py HOOKS), on libbtctl's thread: a phone's stream start, stop
 * and track events open stock's receiver page, which stops the player and takes the headset's keys
 * (headset_poll's bt__recv_pageflag). While Guest Play is on the guest's stream is q2-guest's. */
int guest_a2dp(int event, void *data, void *user) {
    if (gp.on) return 0;
    return stock_btrecv_trampoline(event, data, user);
}
