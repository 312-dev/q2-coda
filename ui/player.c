/* The player, through demo while demo still owns it (docs/ui-rewrite.md#cutover): commands go
 * down the queue Car Remote and Car Audio use (patch/coda_rc_cmdq.h: one line a command,
 * "<seq> <verb>\n", down the FIFO demo's shim reads), and a list to play is the songs' ids, a line
 * each, in the file the shim reads for a play (patch/coda_rc_shim.c cr_songs). demo keeps the
 * queue open while q2ui has the screen (patch/q2ui_gate.c). A setting of demo's (Control Center's
 * switches, Display's choices) goes instead down a pipe demo gives q2ui alone as it starts it, the fd's
 * number after --ctl, as "set NAME VALUE\n" (patch/q2ui_gate.c ctl): the queue, which the phone's
 * daemon writes too, takes no settings. When q2ui owns the player's socket itself, this file is what
 * changes. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../patch/coda_rc_cmdq.h"
#include "q2ui.h"

#define PLAY_LIST "/tmp/coda-rc.list"
#define PLAY_MAX 20000 /* songs one play may queue (coda_rc_shim.c CR_LIST_MAX) */

const char *player_queue = CRC_CMDQ, *player_list = PLAY_LIST;

int player_send(const char *verb) {
    static unsigned seq;
    char line[CRC_CMDQ_LINE];
    if (!seq) seq = (unsigned)(mono_us() / 1000u) | 1; /* never a number an earlier q2ui used */
    int n = snprintf(line, sizeof line, "%u %s\n", ++seq & 0x3fffffff, verb);
    return n > 0 && n < (int)sizeof line ? crc_cmdq_send(player_queue, line, (unsigned)n) : -1;
}

int player_ctl = -1;

int player_set(const char *name, int value) {
    char line[64]; /* patch/q2ui_gate.c Q2UI_CTL_LINE: one write, whole */
    int n = snprintf(line, sizeof line, "set %s %d\n", name, value);
    if (player_ctl < 0 || n <= 0 || n >= (int)sizeof line) return -1;
    return write(player_ctl, line, (size_t)n) == n ? 0 : -1;
}

/* demo's own click for the wheel and keys q2ui reads (patch/q2ui_gate.c ctl): Key Tone and where the click
 * sounds stay demo's rule. The pipe never waits (O_NONBLOCK): a click it has no room for is dropped. */
void player_click(int button) {
    if (player_ctl >= 0) (void)!write(player_ctl, button ? "click 1\n" : "click 0\n", 8);
}

/* A verb and its word down the same pipe (patch/q2ui_gate.c ctl), one write, whole: the Wi-Fi page's search ("scan")
 * and join of a network by its name ("join NAME", its backslashes and newlines as \\ and \n; patch/wifi_flows.c), the
 * Bluetooth page's scan ("btscan"), connect or disconnect ("btlink MAC") and forget ("btforget MAC"; patch/bt_ui.c). */
int player_line(const char *verb, const char *ssid) {
    char line[80]; /* patch/q2ui_gate.c Q2UI_CTL_LINE */
    int n = snprintf(line, sizeof line, "%s", verb);
    for (int i = 0; ssid && ssid[i] && i < 32 && n < (int)sizeof line - 3; ++i) {
        if (!i) line[n++] = ' ';
        if (ssid[i] == '\\' || ssid[i] == '\n') line[n++] = '\\';
        line[n++] = ssid[i] == '\n' ? 'n' : ssid[i];
    }
    line[n++] = '\n';
    if (player_ctl < 0) return -1;
    return write(player_ctl, line, (size_t)n) == n ? 0 : -1;
}

int player_play(const int *ids, int n, int at, int shuffle) {
    if (n > PLAY_MAX) { /* the part of the list round the song chosen */
        int from = at - PLAY_MAX / 2 < 0 ? 0 : at - PLAY_MAX / 2;
        if (from + PLAY_MAX > n) from = n - PLAY_MAX;
        ids += from, at -= from, n = PLAY_MAX;
    }
    char *text = malloc((size_t)n * 12 + 1), tmp[300], verb[48];
    if (!text) return -1;
    size_t len = 0;
    for (int i = 0; i < n; ++i) len += (size_t)sprintf(text + len, "%d\n", ids[i]);
    /* whole or not at all: written beside it and renamed over it */
    snprintf(tmp, sizeof tmp, "%s.q2ui", player_list);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600), ok = fd >= 0 && write(fd, text, len) == (ssize_t)len;
    if (fd >= 0) close(fd);
    free(text);
    if (!ok || rename(tmp, player_list)) return -1;
    snprintf(verb, sizeof verb, "play %d %d %d", at, shuffle, n);
    return player_send(verb);
}
