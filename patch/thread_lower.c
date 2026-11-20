/* A worker thread below the UI and the player (thread_lower), through the o32 syscalls demo does
 * not import. */
#include "peq.h"

/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). demo
 * imports none of gettid, setpriority or ioprio_set. */
#ifndef PEQ_HOST
__asm__(".globl cf_syscall\ncf_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int cf_syscall(int nr, int a, int b, int c);
#else
__attribute__((weak)) int cf_syscall(int nr, int a, int b, int c) { return -1; } /* the tests' */
#endif
#define SYS_SETPRIORITY 4097 /* o32; 4096 is getpriority */
#define SYS_GETTID 4222
#define SYS_IOPRIO_SET 4314
#define IOPRIO(class, level) ((class) << 13 | (level)) /* idle 3, best effort 2 */

/* The calling thread below the UI and the player: nice 19, its reads in the idle I/O class if idle
 * and the kernel takes it, else in best effort's lowest level; the processes it forks (ffmpeg)
 * inherit both. The song durations' worker asks for idle; playlist_art.c's and the slideshow's
 * don't, as they take stock's art locks, which the UI thread waits on, and an idle-class holder
 * gets the disk only when no one else wants it. Returns what took: 1 for nice 19, plus the class
 * << 1. Not static, so the compiler keeps the return its callers ignore: test/patch.py runs it. */
__attribute__((noinline)) int thread_lower(int idle) {
    int tid = cf_syscall(SYS_GETTID, 0, 0, 0), io = 0;
    int nice = !cf_syscall(SYS_SETPRIORITY, 0 /* PRIO_PROCESS */, tid, 19);
    if (idle && !cf_syscall(SYS_IOPRIO_SET, 1 /* IOPRIO_WHO_PROCESS */, tid, IOPRIO(3, 0)))
        io = 3;
    else if (!cf_syscall(SYS_IOPRIO_SET, 1, tid, IOPRIO(2, 7)))
        io = 2;
    return nice | io << 1;
}
