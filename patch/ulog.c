/* demo's lines in the unified log (docs/internals.md#unified-log). demo never writes the card log
 * itself: ulog puts a whole line, stamped as ulog_line.h has it, on its stderr, which S90play keeps
 * in /tmp/q2-demo.err (/tmp/q2-demo.log in a --dev build), and q2guard, the log's one writer, reads
 * it from there each tick. ulog_end is a clean end's last word: it asks q2guard for a snapshot and
 * a flush now (/tmp/q2guard.snap, then a SIGUSR1 to the pid in /tmp/q2guard.lock, through the
 * shell's kill: demo imports no kill of its own) and waits for the answer, the request's removal,
 * at most ULOG_END_MS, so the lines are on the card before the power-off, Restart or update takes
 * the Q2 down. ulog_flush asks for the flush alone, before LocalSend sends the logs; ulog_request_flush asks for it
 * and does not wait (card_release.c, once the card is back after Standby). */
#include "navigation.h"
#include "ulog_line.h"

#define ULOG_SNAP "/tmp/q2guard.snap"
#define ULOG_LOCK "/tmp/q2guard.lock"
#define ULOG_END_MS 2000 /* the most a power-off waits for q2guard's flush */
#define ULOG_FLUSH_MS 1000 /* the most Send Logs waits for it */
#define ULOG_POLL_MS 50

static int ulog_ended __attribute__((section(".scratch"))); /* ulog_end ran: once a run */

void ulog(char level, const char *src, const char *msg) {
    struct {
        long sec, nsec;
    } t = { 0, 0 };
    clock_gettime(1 /* CLOCK_MONOTONIC */, (void *)&t);
    char line[600];
    unsigned up = (unsigned)t.sec * 1000u + (unsigned)(t.nsec / 1000000);
    int n = ulog_line(line, sizeof line, time(0), up, level, src, msg, (int)strlen(msg));
    if (n > 0) write(2, line, (unsigned)n);
}

/* q2guard's snapshot and flush for why ("flush": the flush alone), waited for at most ms. */
static void ulog_ask(const char *why, int ms) {
    int fd = open(ULOG_SNAP, 0x80301, 0644); /* O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC */
    if (fd < 0) return;
    write(fd, why, strlen(why));
    close(fd);
    char pid[16] = { 0 };
    fd = open(ULOG_LOCK, 0x80000); /* O_RDONLY | O_CLOEXEC */
    if (fd >= 0) read(fd, pid, sizeof pid - 1), close(fd);
    int p = atoi(pid);
    char cmd[48];
    tk_snprintf(cmd, sizeof cmd, "kill -USR1 %d 2>/dev/null", p);
    if (p <= 1 || system(cmd)) { /* no q2guard to answer: no request left behind */
        unlink(ULOG_SNAP);
        return;
    }
    for (int waited = 0; waited < ms && !access(ULOG_SNAP, 0); waited += ULOG_POLL_MS)
        usleep(ULOG_POLL_MS * 1000);
}

void ulog_end(const char *why) {
    if (ulog_ended) return;
    ulog_ended = 1;
    char b[96];
    tk_snprintf(b, sizeof b, "shutdown: %s", why);
    ulog('I', "ui", b);
    ulog_ask(why, ULOG_END_MS);
}

/* LocalSend's Send Logs: the logs on the card as they are now, before they are sent; the UI waits for it at most
 * ULOG_FLUSH_MS (q2guard answers on its next tick). */
void ulog_flush(void) { ulog_ask("flush", ULOG_FLUSH_MS); }

/* The flush asked for, not waited for: q2guard answers on its own (card_release.c card_back). */
void ulog_request_flush(void) { ulog_ask("flush", 0); }
