/* Car Remote's command queue (docs/internals.md#car-remote-commands): the commands for demo's shim
 * (patch/coda_rc_shim.c), from the coda-rc daemon (a phone's) and from q2-iap (the car's buttons), go down
 * one FIFO, CRC_CMDQ, which the shim makes and reads from a thread of its own. Each command is one line,
 * "<seq> <verb>\n", shorter than CRC_CMDQ_LINE, written by one write(): a FIFO keeps every such write
 * whole and in order, so two presses close together are two commands, and the shim's loop is woken by
 * the line itself rather than reading a file on a timer. With no reader (no shim yet, or an older demo) the
 * FIFO refuses the open and the sender writes the single command file as before. */
#ifndef CODA_RC_CMDQ_H
#define CODA_RC_CMDQ_H

#define CRC_CMDQ "/tmp/coda-rc.cmdq"
#define CRC_CMDQ_LINE 64 /* a line, its '\n' and state_read's leading byte; a longer one is dropped */
/* The shim's answer "taken, still at work" (coda_rc_shim.c cr_run): a play or a radio, whose work runs on a timer
 * and can hold demo's loop for seconds (9 s on a loaded emulator, 2026-11-23); the daemon then waits ACK_TAKEN_MS
 * (7 s) for the answer that follows, instead of ACK_MS (4 s) (coda_rc.c ack_poll). */
#define CRC_ACK_TAKEN 9

#ifdef O_NONBLOCK
/* line (n bytes, ending '\n') down fifo: 0 sent; -1 not (no reader, no FIFO, too long), and the caller
 * writes the command file instead. Never waits: a full FIFO refuses too. */
static inline int crc_cmdq_send(const char *fifo, const char *line, unsigned n) {
    if (n + 1 >= CRC_CMDQ_LINE || !n || line[n - 1] != '\n') return -1;
    int fd = open(fifo, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    int ok = write(fd, line, n) == (ssize_t)n;
    close(fd);
    return ok ? 0 : -1;
}
#endif

#endif
