/* q2player [-ao alsa]: demo starts it as it starts hciplayer, and the arguments are accepted and unused.
 *
 * While both players ship (docs/player-plan.md#architecture, "The fallback"), this program is installed as
 * /usr/bin/hciplayer and Q2 Pod's patched hciplayer as STOCK_PLAYER. Without the switch file it runs that one, with
 * the same arguments: argv[0] stays "hciplayer", and since that file is named hciplayer too the kernel's process name
 * (comm) is "hciplayer" for either, so demo's `nice -n -5 hciplayer -ao alsa &` and `killall -9 hciplayer` work the
 * same on both. If it cannot be run, this program plays. */
#include "q2player.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>

#define SWITCH_FILE "/mnt/data/q2player" /* `touch` it over q2remote and restart the player to turn q2player on */
#define STOCK_PLAYER "/usr/lib/hciplayer-stock/hciplayer"
/* There while q2player is the player: the payload's Clicker (patch/clicker.c) sends it clicks to mix in. */
#define CLICKS_FILE "/tmp/q2clicks"

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    (void)argc;
    if (access(SWITCH_FILE, F_OK) != 0 && access(STOCK_PLAYER, X_OK) == 0) {
        unlink(CLICKS_FILE);
        execv(STOCK_PLAYER, argv);
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
