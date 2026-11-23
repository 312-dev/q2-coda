/* The card's changes as events (docs/internals.md#card-watch): what Home's status row and its
 * Folders row read from the card (theme_engine.c card_bins: the Rockbox and Groovebox binaries;
 * home.c home_folders: the card and its root) is looked at again when it may have changed, not on a
 * clock. A thread waits in poll() on an inotify descriptor watching the card's root,
 * /mnt/mmc/.rockbox and /mnt/mmc/.groovebox for entries made, removed or moved, and on
 * /proc/mounts, which the kernel marks (POLLPRI) whenever a file system is mounted or unmounted: a
 * card put in, taken out, or handed to USB storage and back. Each change counts in a generation the
 * two caches compare, after the watches are set again (a new .rockbox, the card's new mount), so a
 * binary copied in before its folder's watch stood is still seen by the look that follows. Nothing
 * on the card or in the mount table, and the thread sleeps. Without inotify, /proc/mounts or the
 * thread, card_watch_gen answers 0 and the caches look again every CARD_RECHECK_MS, as they did
 * before. */
#include "navigation.h"

#define SYS_POLL 4188 /* o32 */
#define SYS_INOTIFY_ADD_WATCH 4285
#define SYS_INOTIFY_INIT1 4329
#define CW_CLOEXEC 0x80000 /* O_CLOEXEC on MIPS */
/* IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF, on folders
 * only */
#define CW_MASK (0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800 | 0x01000000)
#define CW_MOUNTS "/proc/mounts"

/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). */
#ifndef PEQ_HOST
__asm__(".globl cw_syscall\ncw_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int cw_syscall(int nr, long a, long b, long c);
#else
__attribute__((weak)) int cw_syscall(int nr, long a, long b, long c) { return -1; }
#endif

static struct {
    volatile int state; /* 0 not started, 1 the thread watches, -1 no events: the caches' clock */
    int in, mounts;     /* the inotify descriptor; /proc/mounts open */
    volatile unsigned gen; /* changes counted, 1 on from the thread's first look */
} cw __attribute__((section(".scratch")));

/* The watches, while the card is mounted: its root, and each app folder that is there. Setting one
 * that stands again changes nothing. */
static void cw_arm(void) {
    if (!card_mounted_now()) return; /* card_mounted's answer follows this thread's generation */
    static const char *const dirs[] = { "/mnt/mmc", "/mnt/mmc/.rockbox", "/mnt/mmc/.groovebox" };
    for (unsigned i = 0; i < sizeof dirs / sizeof *dirs; ++i)
        cw_syscall(SYS_INOTIFY_ADD_WATCH, cw.in, (long)dirs[i], CW_MASK);
}

/* One wait and what it found: 1 to wait again, 0 when a descriptor failed (the thread ends).
 * /proc/mounts is opened again after its mark, which a read would also clear: a new open starts
 * from the table as it is. */
static int cw_step(void) {
    struct {
        int fd;
        short events, revents;
    } p[2] = { { cw.in, 1 /* POLLIN */, 0 }, { cw.mounts, 2 /* POLLPRI */, 0 } };
    int r = cw_syscall(SYS_POLL, (long)p, 2, -1);
    if (r < 0) return 1; /* a signal */
    if ((p[0].revents | p[1].revents) & (0x20 /* POLLNVAL */)) return 0;
    if (p[0].revents & 1) {
        char b[512];
        if (read(cw.in, b, sizeof b) <= 0) return 0;
    }
    if (p[1].revents & (2 | 8 /* POLLERR */)) {
        close(cw.mounts);
        if ((cw.mounts = open(CW_MOUNTS, CW_CLOEXEC)) < 0) return 0;
    }
    cw_arm();
    ++cw.gen;
    return 1;
}

static void *cw_thread(void *unused) {
    while (cw_step()) {}
    cw.state = -1;
    return 0;
}

/* The generation, 0 while the watch is not running; starts nothing, so any thread may ask
 * (system.c card_mounted). */
unsigned card_watch_seen(void) { return cw.state > 0 ? cw.gen : 0; }

/* The generation of what the card holds, 0 while the caches must keep their clock. The first call
 * starts the watch (the UI thread, on a paint). */
unsigned card_watch_gen(void) {
    if (!cw.state) {
        unsigned long t;
        cw.state = -1;
        cw.in = cw_syscall(SYS_INOTIFY_INIT1, CW_CLOEXEC, 0, 0);
        cw.mounts = cw.in > 2 ? open(CW_MOUNTS, CW_CLOEXEC) : -1;
        if (cw.mounts > 2) {
            cw_arm();
            cw.gen = 1;
            cw.state = 1;
            if (!pthread_create(&t, 0, cw_thread, 0) && t) {
                pthread_detach(t);
                return cw.gen;
            }
            cw.state = -1;
        }
        if (cw.mounts > 2) close(cw.mounts);
        if (cw.in > 2) close(cw.in);
    }
    return cw.state > 0 ? cw.gen : 0;
}
