/* q2player [-ao alsa]: demo starts it as it starts hciplayer, and the arguments are accepted and unused.
 *
 * This program is installed as /usr/bin/hciplayer, and Q2 Muse's patched hciplayer, xz-compressed for room in the root
 * filesystem, as STOCK_XZ: the stock player, kept as the fallback (docs/player-plan.md#architecture, "The fallback").
 * The stock player plays instead when SWITCH_FILE is there (Settings > General > Player, patch/player_choice.c) or when
 * q2player has crashed CRASH_LIMIT times in a row this boot (crashed, below). Then this program unpacks it once a boot,
 * with BusyBox's unxz, to STOCK_PLAYER in RAM (/tmp, a tmpfs) and runs it with the same arguments: argv[0] stays
 * "hciplayer", and since that file is named hciplayer too the kernel's process name (comm) is "hciplayer" for either,
 * so demo's `nice -n -5 hciplayer -ao alsa &` and `killall -9 hciplayer` work the same on both. If it cannot be
 * unpacked or run, this program plays. Every path is under PEQ_ROOT, the session's directory in the host tests. */
#include "q2player.h"
#include "ulog_line.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>

#define SWITCH_FILE PEQ_ROOT "/mnt/data/stock-player" /* there: the stock player plays (patch/player_choice.c) */
#define STOCK_XZ PEQ_ROOT "/usr/lib/hciplayer-stock/hciplayer.xz"
/* A directory of its own, so the file is named hciplayer; made by a rename once whole, and kept until the next boot. */
#define STOCK_DIR PEQ_ROOT "/tmp/hciplayer-stock"
#define STOCK_PLAYER STOCK_DIR "/hciplayer"
/* There while q2player is the player: the payload's Clicker (patch/clicker.c) sends it clicks to mix in. */
#define CLICKS_FILE "/tmp/q2clicks"
/* The crash-loop guard. CRASHES_FILE holds how many times in a row q2player has crashed within HEALTHY_S of its start
 * this boot; at CRASH_LIMIT the stock player plays until the next boot, FALLBACK_FILE says so to the Settings row
 * (patch/player_choice.c), and the unified log has the line (plog). */
#define CRASHES_FILE PEQ_ROOT "/tmp/q2player.crashes"
#define FALLBACK_FILE PEQ_ROOT "/tmp/q2player.fallback"
#define CRASH_LIMIT 3
#define STR_(x) #x
#define STR(x) STR_(x)
#define HEALTHY_S 60

static char self[256];          /* this program's own path, for a crash before demo has it */
static char **args;             /* main's argv */
static time_t started;          /* CLOCK_MONOTONIC seconds at the start */
static volatile sig_atomic_t connected; /* demo's socket is open: demo starts the player again once it closes */

/* STOCK_XZ unpacked to STOCK_PLAYER, unless an earlier start this boot did it: 0, or -1 with errno. BusyBox's unxz on a
 * Q2; the host tests' machines have xz. */
static int unpack_stock(void) {
    const char *part = STOCK_PLAYER ".part";
    int fd, status;
    pid_t pid;
    if (access(STOCK_PLAYER, X_OK) == 0) return 0;
    if (access(STOCK_XZ, R_OK) != 0) return -1;
    mkdir(STOCK_DIR, 0755);
    if ((fd = open(part, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0755)) < 0) return -1;
    if ((pid = fork()) == 0) {
        dup2(fd, 1);
        execl("/bin/busybox", "unxz", "-c", STOCK_XZ, (char *)NULL);
        execlp("xz", "xz", "-dc", STOCK_XZ, (char *)NULL);
        _exit(127);
    }
    close(fd);
    if (pid < 0 || waitpid(pid, &status, 0) != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        unlink(part); /* a full /tmp or a damaged file: nothing half written is ever run */
        errno = EIO;
        return -1;
    }
    return rename(part, STOCK_PLAYER);
}

/* A line in the unified log (docs/internals.md#unified-log): stamped as ulog_line.h has it, source "player", on stderr,
 * which is demo's (S90play keeps it), where q2guard reads it. Async-signal-safe: crashed writes one too. */
static void plog(char level, const char *msg, int n) {
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

/* A fatal signal: counted (one more, or the first again after HEALTHY_S of play), and logged. Before demo has the
 * socket nothing would start the player again, so the program starts itself again, and that start decides; after,
 * the process dies of the signal as it would have, and demo starts the player again (docs/player.md#start). Only
 * async-signal-safe calls. */
static void crashed(int sig) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int n = now.tv_sec - started < HEALTHY_S ? crashes() + 1 : 1;
    char line[] = "q2player: crashed, signal 00, crash 0 of " STR(CRASH_LIMIT) " in a row";
    char *at = line + 26; /* the signal's two digits; the count's is ten on */
    at[0] = '0' + sig / 10 % 10, at[1] = '0' + sig % 10, at[10] = '0' + (n > 9 ? 9 : n);
    int fd = open(CRASHES_FILE, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) {
        if (write(fd, at + 10, 1) < 0) at[10] = '?'; /* the log says the count was not kept */
        close(fd);
    }
    plog('E', line, sizeof line - 1);
    if (!connected && self[0] && n <= CRASH_LIMIT) execv(self, args); /* bounded: no stock player, no loop */
    signal(sig, SIG_DFL);
    raise(sig);
}

/* Whether the stock player plays: chosen, or q2player's crashes this boot. A fallback is logged once a boot. */
static int stock_plays(void) {
    if (access(SWITCH_FILE, F_OK) == 0) return 1;
    int n = crashes();
    if (n < CRASH_LIMIT) return 0;
    if (access(FALLBACK_FILE, F_OK) != 0) {
        char line[96];
        plog('W', line, snprintf(line, sizeof line, "q2player: crashed %d times in a row; the stock player plays until the next restart", n));
        close(open(FALLBACK_FILE, O_WRONLY | O_CREAT | O_CLOEXEC, 0644));
    }
    return 1;
}

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    struct timespec now;
    (void)argc;
    args = argv;
    setvbuf(stderr, NULL, _IOLBF, 0);
    if (access(STOCK_XZ, F_OK) == 0 && stock_plays()) {
        unlink(CLICKS_FILE);
        unlink(SCAN_FILE);
        if (unpack_stock() == 0) execv(STOCK_PLAYER, argv);
        fprintf(stderr, "q2player: %s: %s; playing with q2player\n", STOCK_PLAYER, strerror(errno));
    }
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    self[n > 0 && (size_t)n < sizeof self - 1 ? n : 0] = 0;
    clock_gettime(CLOCK_MONOTONIC, &now);
    started = now.tv_sec;
    struct sigaction sa = { .sa_handler = crashed, .sa_flags = SA_NODEFER | SA_RESETHAND };
    sigemptyset(&sa.sa_mask);
    for (unsigned i = 0; i < 5; i++) sigaction((int[]){ SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT }[i], &sa, NULL);
    close(open(CLICKS_FILE, O_WRONLY | O_CREAT | O_CLOEXEC, 0644));
    signal(SIGPIPE, SIG_IGN);
    /* demo listens before it starts the player, but a player restarted by hand may be early */
    for (int i = 0; ipc_connect(path ? path : "/tmp/sock_play") < 0; i++) {
        if (i == 50) return 1;
        usleep(100000);
    }
    connected = 1;
    if (pthread_create(&engine, NULL, engine_thread, NULL)) return 1;
    ipc_thread(NULL);
    return 0;
}
