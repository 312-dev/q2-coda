/* LD_PRELOAD'ed by q2-heavy (tools/ci/q2-heavy): marks each process it starts non-dumpable before main.
 * WSL pipes a crashed process's core to the Windows drive, and a pipe ignores RLIMIT_CORE (ulimit -c 0
 * and 1 both still dump: measured 2026-11-16 on kernel 6.18 with a pipe core_pattern); a 33 GB core
 * filled C: and took the Linux disk with it. PR_SET_DUMPABLE 0 is honoured by every core_pattern.
 * execve resets the flag, so every program a job runs loads this again through LD_PRELOAD. */
#include <sys/prctl.h>

__attribute__((constructor)) static void nodump(void) { prctl(PR_SET_DUMPABLE, 0, 0, 0, 0); }
