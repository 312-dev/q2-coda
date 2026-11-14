/* q2player [-ao alsa]: demo starts it as it starts hciplayer, and the arguments are accepted and unused. */
#include "q2player.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const char *path = getenv("Q2PLAYER_SOCK");
    pthread_t engine;
    (void)argc, (void)argv;
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
