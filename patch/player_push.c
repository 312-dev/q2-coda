/* q2player's status push (docs/internals.md#player-status-push). Stock asks the player for its
 * state on two clocks while a track is loaded, whatever the screen shows: mclSyncPlayerStatus'
 * thread every 200 ms and ringnav_mainscheduling (playback_power.c) every 100 ms. q2player sends a
 * one-byte datagram to PUSH_PATH each time what those questions would get back changes, and after
 * each event it sends (player/ipc.c ipc_status_note, ipc_event): the play state, the song's
 * details, the position's whole second, a song's exit. Bound here, the socket lets both wait for a
 * change instead: the status thread sleeps in poll() on it for up to DARK_POLL_US, and the
 * scheduling runs on the UI pass after a push, else DARK_POLL_US after its last run. Paused with
 * nothing moving, each asks once a second; playing, once a second as the position's second turns.
 * Without the socket (the kernel refused it, or the bind failed) both keep stock's pace. A datagram
 * can only make the thread look sooner, and never more often than stock's own wait asked. */
#include "navigation.h"

#define PUSH_PATH "/tmp/q2player.push"
#define SYS_BIND 4169 /* o32 */
#define SYS_POLL 4188
#define PUSH_SOCK (1 | 0x80000) /* SOCK_DGRAM (MIPS numbering) | SOCK_CLOEXEC */
#define MSG_DONTWAIT 0x40

/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). */
#ifndef PEQ_HOST
__asm__(".globl pp_syscall\npp_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int pp_syscall(int nr, long a, long b, long c);
#else
__attribute__((weak)) int pp_syscall(int nr, long a, long b, long c) { return -1; }
#endif

static struct {
    volatile int fd;       /* the bound socket, plus one; 0 not made yet, -1 refused */
    volatile unsigned seq; /* pushes taken by the status thread */
    unsigned at;           /* time_now_ms when the status thread's last wait ended */
} pp __attribute__((section(".scratch")));

/* The socket, bound on the UI thread's first look (player_push_live), before the status thread may
 * wait on it. One a previous demo left is unlinked first. */
static int pp_fd(void) {
    if (!pp.fd) {
        struct {
            unsigned short family;
            char path[sizeof PUSH_PATH];
        } a = { 1 /* AF_UNIX */, PUSH_PATH };
        int fd = socket(1, PUSH_SOCK, 0);
        if (fd > 0) unlink(PUSH_PATH);
        if (fd > 0 && !pp_syscall(SYS_BIND, fd, (long)&a, sizeof a)) {
            pp.fd = fd + 1;
        } else {
            if (fd > 0) close(fd);
            pp.fd = -1;
        }
    }
    return pp.fd > 0 ? pp.fd - 1 : -1;
}

/* The UI thread: the push is there to wait on, and how many the status thread has taken. */
int player_push_live(void) { return pp_fd() >= 0; }
unsigned player_push_seq(void) { return pp.seq; }

/* The status thread's wait for stock's us (ringnav_dark_usleep): until a push, at most
 * DARK_POLL_US; a push sooner than us after the last wait ended waits out the rest of us. */
int player_push_wait(unsigned us) {
    int fd = pp_fd();
    if (fd < 0) return usleep(us);
    struct {
        int fd;
        short events, revents;
    } p = { fd, 1 /* POLLIN */, 0 };
    int r = pp_syscall(SYS_POLL, (long)&p, 1, DARK_POLL_US / 1000);
    if (r < 0) return usleep(us); /* a signal, or a poll the kernel refused: stock's wait */
    if (r > 0) {
        char b[16];
        while (recv(fd, b, sizeof b, MSG_DONTWAIT) > 0) {}
        ++pp.seq;
        unsigned spent = (unsigned)time_now_ms() - pp.at;
        if (spent < us / 1000) usleep(us - spent * 1000);
    }
    pp.at = (unsigned)time_now_ms();
    return 0;
}
