/* q2player [-ao alsa]: the player, installed as /usr/bin/hciplayer, so demo's `nice -n -5 hciplayer -ao alsa &` starts
 * it and `killall -9 hciplayer` stops it (docs/player.md#start); the arguments are accepted and unused. */
#include "q2player.h"
#include "peq.h" /* PEQ_ROOT: the Q2's paths, or a host test's directory */
#include "ulog_line.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>

/* There while q2player is the player: the payload's Clicker (patch/clicker.c) sends it clicks to mix in. */
#define CLICKS_FILE PEQ_ROOT "/tmp/q2clicks"
/* Crashes in a row: CRASHES_FILE holds how many times q2player has crashed within HEALTHY_S of its start this boot, and
 * each crash's line in the unified log (plog) says which signal and how many in a row. demo starts the player again. */
#define CRASHES_FILE PEQ_ROOT "/tmp/q2player.crashes"
#define HEALTHY_S 60

static time_t started; /* CLOCK_MONOTONIC seconds at the start */

/* A line in the unified log (docs/internals.md#unified-log): stamped as ulog_line.h has it, source "player", on stderr,
 * which is demo's (S90play keeps it), where q2guard reads it. Async-signal-safe: crashed writes one too. */
void plog(char level, const char *msg, int n) {
    struct timespec wall, up;
    char line[256];
    clock_gettime(CLOCK_REALTIME, &wall);
    clock_gettime(CLOCK_MONOTONIC, &up);
    int len = ulog_line(line, sizeof line, wall.tv_sec, (unsigned)(up.tv_sec * 1000 + up.tv_nsec / 1000000), level,
                        "player", msg, n);
    if (len > 0 && write(2, line, (size_t)len) < 0) { /* nowhere else to say it */
    }
}

/* CRASHES_FILE's count, 0 when there is none. Async-signal-safe: crashed reads it too. */
static int crashes(void) {
    char c = '0';
    int fd = open(CRASHES_FILE, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    if (read(fd, &c, 1) != 1 || c < '0' || c > '9') c = '0';
    close(fd);
    return c - '0';
}

/* A fatal signal: counted (one more, or the first again after HEALTHY_S of play) and logged, then the process dies of
 * it as it would have. Only async-signal-safe calls. */
static void crashed(int sig) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int n = now.tv_sec - started < HEALTHY_S ? crashes() + 1 : 1;
    char line[] = "q2player: crashed, signal 00, crash 0 in a row";
    char *at = line + 26; /* the signal's two digits; the count's is ten on */
    at[0] = '0' + sig / 10 % 10, at[1] = '0' + sig % 10, at[10] = '0' + (n > 9 ? 9 : n);
    int fd = open(CRASHES_FILE, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) {
        if (write(fd, at + 10, 1) < 0) at[10] = '?'; /* the log says the count was not kept */
        close(fd);
    }
    plog('E', line, sizeof line - 1);
    signal(sig, SIG_DFL);
    raise(sig);
}

/* SIGUSR2 is mcl-carrelease for a script (patch/q2car.sh before it unbinds the gadget): blocked in every thread and
 * taken here, where setting the request and waking the playback thread is safe */
static void *release_thread(void *arg) {
    sigset_t *set = arg;
    for (int sig; !sigwait(set, &sig);) {
        pthread_mutex_lock(&ctl.mu);
        ctl.car_release = 1;
        pthread_cond_broadcast(&ctl.wake);
        pthread_mutex_unlock(&ctl.mu);
    }
    return NULL;
}

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    struct timespec now;
    (void)argc, (void)argv;
    setvbuf(stderr, NULL, _IOLBF, 0);
    clock_gettime(CLOCK_MONOTONIC, &now);
    started = now.tv_sec;
    struct sigaction sa = { .sa_handler = crashed, .sa_flags = SA_NODEFER | SA_RESETHAND };
    sigemptyset(&sa.sa_mask);
    for (unsigned i = 0; i < 5; i++) sigaction((int[]){ SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT }[i], &sa, NULL);
    close(open(CLICKS_FILE, O_WRONLY | O_CREAT | O_CLOEXEC, 0644));
    signal(SIGPIPE, SIG_IGN);
    /* demo listens before it starts the player, but a player restarted by hand may be early */
    for (int i = 0; ipc_connect(path ? path : PEQ_ROOT "/tmp/sock_play") < 0; i++) {
        if (i == 50) return 1;
        usleep(100000);
    }
    const char *push = getenv("Q2PLAYER_PUSH");
    ipc_push_open(push ? push : PEQ_ROOT "/tmp/q2player.push");
    static sigset_t release;
    sigemptyset(&release);
    sigaddset(&release, SIGUSR2);
    pthread_sigmask(SIG_BLOCK, &release, NULL); /* before any thread: each inherits it */
    pthread_t releaser;
    if (!pthread_create(&releaser, NULL, release_thread, &release)) pthread_detach(releaser);
    feed_start(); /* the car's other sources (feed.c) */
    if (pthread_create(&engine, NULL, engine_thread, NULL)) return 1;
    ipc_thread(NULL);
    return 0;
}
