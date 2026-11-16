/* q2player [-ao alsa]: demo starts it as it starts hciplayer, and the arguments are accepted and unused.
 *
 * While both players ship (docs/player-plan.md#architecture, "The fallback"), this program is installed as
 * /usr/bin/hciplayer and Q2 Muse's patched hciplayer, xz-compressed for room in the root filesystem, as STOCK_XZ.
 * Without the switch file it unpacks that once a boot, with BusyBox's unxz, to STOCK_PLAYER in RAM (/tmp, a tmpfs) and
 * runs it with the same arguments: argv[0] stays "hciplayer", and since that file is named hciplayer too the kernel's
 * process name (comm) is "hciplayer" for either, so demo's `nice -n -5 hciplayer -ao alsa &` and `killall -9
 * hciplayer` work the same on both. If it cannot be unpacked or run, this program plays. */
#include "q2player.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>

#define SWITCH_FILE "/mnt/data/q2player" /* `touch` it over q2remote and restart the player to turn q2player on */
#define STOCK_XZ "/usr/lib/hciplayer-stock/hciplayer.xz"
/* A directory of its own, so the file is named hciplayer; made by a rename once whole, and kept until the next boot. */
#define STOCK_DIR "/tmp/hciplayer-stock"
#define STOCK_PLAYER STOCK_DIR "/hciplayer"
/* There while q2player is the player: the payload's Clicker (patch/clicker.c) sends it clicks to mix in. */
#define CLICKS_FILE "/tmp/q2clicks"

/* STOCK_XZ unpacked to STOCK_PLAYER, unless an earlier start this boot did it: 0, or -1 with errno. */
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

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    (void)argc;
    if (access(SWITCH_FILE, F_OK) != 0 && access(STOCK_XZ, F_OK) == 0) {
        unlink(CLICKS_FILE);
        unlink(SCAN_FILE);
        if (unpack_stock() == 0) execv(STOCK_PLAYER, argv);
        fprintf(stderr, "q2player: %s: %s; playing with q2player\n", STOCK_PLAYER, strerror(errno));
    }
    close(open(CLICKS_FILE, O_WRONLY | O_CREAT | O_CLOEXEC, 0644));
    signal(SIGPIPE, SIG_IGN);
    setvbuf(stderr, NULL, _IOLBF, 0);
    /* demo listens before it starts the player, but a player restarted by hand may be early */
    for (int i = 0; ipc_connect(path ? path : "/tmp/sock_play") < 0; i++) {
        if (i == 50) return 1;
        usleep(100000);
    }
    if (pthread_create(&engine, NULL, engine_thread, NULL)) return 1;
    ipc_thread(NULL);
    return 0;
}
