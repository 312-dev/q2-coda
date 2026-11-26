/* The wheel and the buttons, read from their evdev devices by name: "hyn_sliderbar", a finger's
 * place round the ring as ABS_X 1 to 200 a turn, and "md-gpio-keys" (docs/hardware.md). A tick is
 * WHEEL_TICK_UNITS of travel, the payload's default (patch/playback.c wheel_sample); x falls as
 * the finger goes clockwise, which is down a list. The fds are for poll: nothing here waits. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/input.h>

#include "q2ui.h"

#define WHEEL_TICK_UNITS 28
#define WHEEL_TURN 200

static int wheel_fd = -1, keys_fd = -1;
static int touching, have_last, last_x, x, acc;

int input_open(const char *dir) {
    for (int i = 0; i < 8; ++i) {
        char path[160], name[64] = "";
        snprintf(path, sizeof path, "%s/event%d", dir, i);
        /* read and write: a FIFO standing in for the device would wait for its writer */
        int fd = open(path, O_RDWR | O_NONBLOCK);
        if (fd < 0) continue;
        if (ioctl(fd, EVIOCGNAME(sizeof name), name) < 0) {
            /* a host run's FIFOs answer no ioctl: by the emulator's numbering */
            strcpy(name, i == 3 ? "hyn_sliderbar" : i == 1 ? "md-gpio-keys" : "");
        }
        if (!strcmp(name, "hyn_sliderbar") && wheel_fd < 0)
            wheel_fd = fd;
        else if (!strcmp(name, "md-gpio-keys") && keys_fd < 0)
            keys_fd = fd;
        else
            close(fd);
    }
    return wheel_fd >= 0 || keys_fd >= 0 ? 0 : -1;
}

int input_fds(int *fds, int max) {
    int n = 0;
    if (wheel_fd >= 0 && n < max) fds[n++] = wheel_fd;
    if (keys_fd >= 0 && n < max) fds[n++] = keys_fd;
    return n;
}

static int wheel_event(const struct input_event *e, input_t *out, int max) {
    int n = 0;
    if (e->type == EV_ABS && e->code == ABS_X) x = e->value;
    if ((e->type == EV_KEY && e->code == BTN_TOUCH) ||
        (e->type == EV_ABS && e->code == ABS_PRESSURE)) {
        touching = e->value != 0;
        if (!touching) have_last = 0, acc = 0;
    }
    if (e->type != EV_SYN || !touching) return 0;
    if (!have_last) {
        have_last = 1, last_x = x;
        return 0;
    }
    int d = x - last_x;
    d = d < -WHEEL_TURN / 2 ? d + WHEEL_TURN : d > WHEEL_TURN / 2 ? d - WHEEL_TURN : d;
    last_x = x;
    if (d && (d ^ acc) < 0) acc = 0; /* reversed: the carry the other way is dropped */
    acc += d;
    while ((acc >= WHEEL_TICK_UNITS || acc <= -WHEEL_TICK_UNITS) && n < max) {
        int dir = acc > 0 ? 1 : -1;
        acc -= dir * WHEEL_TICK_UNITS;
        out[n++] = (input_t){ IN_TICK, -dir, now_ms() };
    }
    return n;
}

int input_read(input_t *out, int max) {
    struct input_event e[32];
    int n = 0;
    for (;;) {
        long got = wheel_fd >= 0 ? read(wheel_fd, e, sizeof e) : 0;
        if (got < (long)sizeof *e) break;
        for (unsigned i = 0; i < got / sizeof *e; ++i) n += wheel_event(&e[i], out + n, max - n);
    }
    for (;;) {
        long got = keys_fd >= 0 ? read(keys_fd, e, sizeof e) : 0;
        if (got < (long)sizeof *e) break;
        for (unsigned i = 0; i < got / sizeof *e && n < max; ++i)
            if (e[i].type == EV_KEY && e[i].value != 2)
                out[n++] = (input_t){ e[i].value ? IN_KEY_DOWN : IN_KEY_UP, e[i].code, now_ms() };
    }
    return n;
}
