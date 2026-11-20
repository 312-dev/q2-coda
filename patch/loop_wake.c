/* The UI loop's wait (docs/internals.md#the-ui-loops-wait). ringnav_sleep idles the loop between
 * passes; here it waits in poll() on an eventfd instead of a plain sleep, so whatever brings the UI
 * work ends the wait at once: every event an input thread or another thread posts
 * (ringnav_queue_event, the one way into AWTK's queue, posts timers and idles from other threads
 * too), the wheel's raw travel (wheel_sample, for the thumb and the bar's lean, which post
 * nothing), a headset key (headset.c) and BlueZ's volume signal (bt_vol_event.c). With that, the
 * wait may run past ringnav_sleep's fixed idles when nothing needs the loop sooner (loop_span).
 * Without an eventfd (the kernel refused one) it is the plain sleep it replaces, at the same
 * length. */
#include "navigation.h"

#define SYS_EVENTFD2 4325 /* o32 */
#define SYS_POLL 4188
#define EFD_FLAGS (0x80 | 0x80000) /* EFD_NONBLOCK | EFD_CLOEXEC: O_NONBLOCK, O_CLOEXEC on MIPS */

/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). A name of
 * its own, not thread_lower.c's, so test/patch.py's machine can answer the loop's alone. */
#ifndef PEQ_HOST
__asm__(".globl lw_syscall\nlw_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int lw_syscall(int nr, int a, int b, int c);
#else
__attribute__((weak)) int lw_syscall(int nr, int a, int b, int c) { return -1; }
#endif

static struct {
    volatile int fd;      /* the eventfd, plus one; 0 not made yet, -1 refused */
    volatile int waiting; /* the UI thread is in (or about to enter) a long wait */
    unsigned idles;       /* the fewest idles seen, plus one: stock's repeating on_wm_idle_status */
} lw __attribute__((section(".scratch")));

/* Any thread: the UI loop's wait ends now, or its next one at once. */
void loop_wake(void) {
    int fd = lw.fd;
    if (fd > 0) {
        unsigned long long one = 1;
        write(fd - 1, &one, sizeof one);
    }
}
/* The touch thread, per sample: a wake only while the loop idles, so a turn costs no write a sample
 * while the loop runs at stock's pace. A sample that lands just as the loop starts its idle is
 * caught by the next one, about 10 ms later. */
void loop_wake_touch(void) {
    __sync_synchronize();
    if (lw.waiting) loop_wake();
}

/* The UI thread's eventfd, made on the first wait. fd 0 is stdin's, so a 0 back is no eventfd
 * either. */
static int lw_fd(void) {
    if (!lw.fd) {
        int fd = lw_syscall(SYS_EVENTFD2, 0, EFD_FLAGS, 0);
        lw.fd = fd > 0 ? fd + 1 : -1;
    }
    return lw.fd > 0 ? lw.fd - 1 : -1;
}

#define U32(p, o) ((void *)(unsigned long)*(volatile unsigned *)((char *)(p) + (o)))

/* ms until the next AWTK timer is due (0 when one is), or -1 when a frame timer (LOOP_ANIM_MS or
 * less) runs, which keeps the loop's pace. The widget animator manager's timer runs every 8 ms
 * whether it animates anything or not, so it counts only while it has an animator. A suspended
 * timer waits for nothing. */
static int lw_next_timer(unsigned now) {
    void *am = widget_animator_manager(), *tm = timer_manager();
    if (!tm) return -1;
    long long next = -1;
    for (void *node = U32(tm, TM_LIST); node; node = U32(node, 0)) {
        char *t = U32(node, SLIST_DATA);
        if (!t || t[TIMER_SUSPEND]) continue;
        unsigned period = *(unsigned *)(t + TIMER_DURATION);
        if (am && U32(t, TIMER_CTX) == am && !U32(am, AM_FIRST)) continue;
        if (period <= LOOP_ANIM_MS) return -1;
        unsigned start = *(unsigned *)(t + TIMER_START); /* the low word: time_now_ms's clock */
        int left = (int)(start + period - now);
        if (left < 0) left = 0;
        if (next < 0 || left < next) next = left;
    }
    return next < 0 ? 0x7fffffff : (int)next;
}

/* How long ringnav_sleep's idle of base ms may run: base, or up to most when the loop has an
 * eventfd to wake it and nothing needs a pass sooner. Lit (timers set), not past the next AWTK
 * timer due, and not while a poll of this pass left something to paint, the screen's dirty rects
 * being drawn on the next pass. Either way not while a frame timer or an animator runs, while
 * stock's key lockout counts down (on_wm_timer_setup_state, 100 ms a step: the wheel's ticks wait
 * on it), or while an idle other than stock's repeating ones waits. Never less than base, so a
 * timer already due or a suspended one never makes the loop pass more often than its idle. */
unsigned loop_span(unsigned base, unsigned most, int timers) {
    if (lw_fd() < 0 || most <= base) return base;
    unsigned idles = idle_manager_count(idle_manager());
    if (!lw.idles || idles + 1 < lw.idles) lw.idles = idles + 1;
    if (idles + 1 > lw.idles || *(volatile signed char *)KEY_LOCKOUT > 0) return base;
    int next = lw_next_timer((unsigned)time_now_ms());
    if (next < 0) return base;
    if (timers) {
        void *top = window_manager_get_top_window(window_manager()); /* the wm has no window */
        void *nw = top ? widget_get_native_window(top) : 0;
        if (nw && I(nw, NW_DIRTY)) return base;
        if ((unsigned)next < most) most = (unsigned)next;
    }
    return most > base ? most : base;
}

/* Idle the UI loop ms, ended early by loop_wake. */
void loop_wait(unsigned ms) {
    int fd = lw_fd();
    if (fd < 0) {
        sleep_ms(ms);
        return;
    }
    struct {
        int fd;
        short events, revents;
    } p = { fd, 1 /* POLLIN */, 0 };
    lw.waiting = 1;
    __sync_synchronize();
    if (lw_syscall(SYS_POLL, (int)(long)&p, 1, (int)ms) > 0) {
        unsigned long long n;
        read(fd, &n, sizeof n); /* nonblocking: the count back to 0 */
    }
    lw.waiting = 0;
}
