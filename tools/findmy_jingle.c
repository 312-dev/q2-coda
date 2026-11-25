/* Find My Vamp's jingle alone, to audition on a Q2 before a build: patch/findmy_tune.h's phrase, played twice through
 * /dev/jz_pwm as the payload's findmy.c plays it, then the buzzer left low and released. Static, no libc (raw o32
 * system calls), so it runs on the Q2's rootfs as it is (docs/internals.md#find-my-vamp):
 *
 *   clang --target=mipsel-linux-gnu -march=mips32r2 -mabi=32 -mfp64 -mnan=2008 -G0 -fno-pic -mno-abicalls \
 *       -static -nostdlib -fuse-ld=lld -O2 -o jingle tools/findmy_jingle.c
 *
 * nan2008 and fp64 are the ELF flags the Q2's kernel takes; -G0 keeps globals off $gp and -fno-pic -mno-abicalls
 * keep calls off it too, as no start code sets it.
 * Exit status: 0 played, 1 no /dev/jz_pwm, 2 the channel refused. */
static long sys3(long n, long a, long b, long c) {
    register long v0 __asm__("$2") = n;
    register long a0 __asm__("$4") = a, a1 __asm__("$5") = b, a2 __asm__("$6") = c;
    register long a3 __asm__("$7");
    __asm__ volatile("syscall"
                     : "+r"(v0), "=r"(a3)
                     : "r"(a0), "r"(a1), "r"(a2)
                     : "$1", "$3", "$8", "$9", "$10", "$11", "$12", "$13", "$14", "$15", "$24", "$25", "hi", "lo",
                       "memory");
    return a3 ? -v0 : v0;
}
#define SYS_EXIT 4001
#define SYS_OPEN 4005
#define SYS_IOCTL 4054
#define SYS_NANOSLEEP 4166

static int sleep_ms(unsigned ms) {
    long ts[2] = { ms / 1000, (long)(ms % 1000) * 1000000 };
    sys3(SYS_NANOSLEEP, (long)ts, 0, 0);
    return 0;
}
#define FM_IOCTL(fd, request, arg) ((int)sys3(SYS_IOCTL, fd, (long)(request), (long)(arg)))
#define FM_WAIT(ms) sleep_ms(ms)
#include "../patch/findmy_tune.h"

void __start(void) {
    int fd = (int)sys3(SYS_OPEN, (long)"/dev/jz_pwm", 2, 0);
    if (fd < 0) sys3(SYS_EXIT, 1, 0, 0);
    int ch = fm_take(fd);
    if (ch < 0) sys3(SYS_EXIT, 2, 0, 0);
    fm_phrase(fd, &ch);
    fm_phrase(fd, &ch);
    fm_done(fd, ch);
    sys3(SYS_EXIT, 0, 0, 0);
}
