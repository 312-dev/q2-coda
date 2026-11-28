/* An evenly paced finger for device reads of q2ui's frame rate (ui/tools/q2drag.c): writes input events into a Q2's
 * wheel or touch panel device on an absolute clock, so every step lands its period after the last whatever the
 * shell or the scheduler did, where a shell loop forking `usleep` per step delivered them in bursts.
 *
 *   q2drag wheel DEVICE STEPS UNITS PERIOD_US       the wheel: a finger down at 100, ABS_X down UNITS a step (round the
 *                                                   ring's 1 to 200), lifted after the last step
 *   q2drag touch DEVICE X0 Y0 X1 Y1 STEPS PERIOD_US a finger on the screen from (X0, Y0) to (X1, Y1), upright
 *                                                   coordinates as ui/input.c reads them (ABS_X 319 - y, ABS_Y x),
 *                                                   ABS_PRESSURE alone as a Q2's panel says a finger
 *
 * Built for the Q2 with the toolchain q2ui is (tools/ui.py), as ~/.cache/q2-xfer/scrollread.py does. */
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int fd;
static void put(int type, int code, int value) {
    struct input_event e;
    memset(&e, 0, sizeof e);
    e.type = (unsigned short)type, e.code = (unsigned short)code, e.value = value;
    if (write(fd, &e, sizeof e) != (ssize_t)sizeof e) perror("q2drag: write"), exit(1);
}
static void at(struct timespec *t, long period_us) {
    t->tv_nsec += period_us * 1000L;
    while (t->tv_nsec >= 1000000000L) t->tv_nsec -= 1000000000L, ++t->tv_sec;
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, t, 0);
}

int main(int argc, char **argv) {
    int wheel = argc == 6 && !strcmp(argv[1], "wheel"), touch = argc == 9 && !strcmp(argv[1], "touch");
    if (!wheel && !touch) {
        fprintf(stderr, "usage: q2drag wheel DEVICE STEPS UNITS PERIOD_US | touch DEVICE X0 Y0 X1 Y1 STEPS PERIOD_US\n");
        return 2;
    }
    fd = open(argv[2], O_WRONLY);
    if (fd < 0) return perror(argv[2]), 1;
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    if (wheel) {
        int steps = atoi(argv[3]), units = atoi(argv[4]), x = 100;
        long period = atol(argv[5]);
        put(EV_ABS, ABS_X, x), put(EV_ABS, ABS_Y, 100), put(EV_ABS, ABS_PRESSURE, 1), put(EV_KEY, BTN_TOUCH, 1);
        put(EV_SYN, 0, 0);
        for (int i = 0; i < steps; ++i) {
            at(&t, period);
            x = ((x - units - 1) % 200 + 200) % 200 + 1;
            put(EV_ABS, ABS_X, x), put(EV_ABS, ABS_Y, 100 + (i & 1)), put(EV_SYN, 0, 0);
        }
        at(&t, period);
        put(EV_ABS, ABS_PRESSURE, 0), put(EV_KEY, BTN_TOUCH, 0), put(EV_SYN, 0, 0);
    } else {
        int x0 = atoi(argv[3]), y0 = atoi(argv[4]), x1 = atoi(argv[5]), y1 = atoi(argv[6]), steps = atoi(argv[7]);
        long period = atol(argv[8]);
        steps = steps < 1 ? 1 : steps;
        for (int i = 0; i <= steps; ++i) {
            if (i) at(&t, period);
            int x = x0 + (x1 - x0) * i / steps, y = y0 + (y1 - y0) * i / steps;
            put(EV_ABS, ABS_X, 319 - y), put(EV_ABS, ABS_Y, x);
            if (!i) put(EV_ABS, ABS_PRESSURE, 1);
            put(EV_SYN, 0, 0);
        }
        at(&t, period);
        put(EV_ABS, ABS_PRESSURE, 0), put(EV_SYN, 0, 0);
    }
    close(fd);
    return 0;
}
