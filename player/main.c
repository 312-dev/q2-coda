/* q2player [-ao alsa]: the player, installed as /usr/bin/hciplayer, so demo's `nice -n -5 hciplayer -ao alsa &` starts
 * it and `killall -9 hciplayer` stops it (docs/player.md#start); the arguments are accepted and unused. */
#include "q2player.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>

/* There while q2player is the player: the payload's Clicker (patch/clicker.c) sends it clicks to mix in. */
#define CLICKS_FILE "/tmp/q2clicks"

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    (void)argc, (void)argv;
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
