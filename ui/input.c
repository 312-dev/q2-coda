/* The wheel, the buttons and the touchscreen, read from their evdev devices by name:
 * "hyn_sliderbar", a finger's place round the ring as ABS_X 1 to 200 a turn, "md-gpio-keys" and
 * "hyn_ts" (docs/hardware.md). A tick is
 * WHEEL_TICK_UNITS of travel, the payload's default (patch/playback.c wheel_sample); x falls as
 * the finger goes clockwise, which is down a list. The fds are for poll: nothing here waits.
 *
 * Between ticks the wheel says how far toward the next one the finger has turned (IN_TUG, the
 * share of a tick, signed as a tick's value is), which is what the selection leans by
 * (docs/oled-design.md#selection-bar); the finger leaving is a tug of 0. A key held KEY_HOLD_MS
 * is IN_KEY_HOLD once, and its release is then not a key up: a hold and a press are two acts.
 *
 * The touchscreen is "hyn_ts": BTN_TOUCH, ABS_X and ABS_Y on the panel's own 320 x 375 portrait axes,
 * which the upright screen turns a quarter (tools/q2remote.py touch_xy): upright x is the panel's y,
 * upright y is 319 less its x. A finger is IN_TOUCH_DOWN at its first place, IN_TOUCH_MOVE at each
 * new one and IN_TOUCH_UP where it was last. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/input.h>

#include "q2ui.h"

#define WHEEL_TICK_UNITS 28
#define WHEEL_TURN 200

static int wheel_fd = -1, keys_fd = -1, touch_fd = -1;
static int touching, have_last, last_x, x, acc;
static struct {
    int code; /* the key that is down, 0 none */
    unsigned at;
    int held; /* its hold was reported */
} down;

static struct {
    int raw_x, raw_y, down, was, sent_x, sent_y;
} ts;

static unsigned now_ms(void) { return (unsigned)(mono_us() / 1000u); }

int input_open(const char *dir) {
    for (int i = 0; i < 8; ++i) {
        char path[160], name[64] = "";
        snprintf(path, sizeof path, "%s/event%d", dir, i);
        /* read and write: a FIFO standing in for the device would wait for its writer */
        int fd = open(path, O_RDWR | O_NONBLOCK);
        if (fd < 0) continue;
        if (ioctl(fd, EVIOCGNAME(sizeof name), name) < 0) {
            /* a host run's FIFOs answer no ioctl: by the emulator's numbering */
            strcpy(name, i == 3 ? "hyn_sliderbar" : i == 1 ? "md-gpio-keys" : i == 2 ? "hyn_ts" : "");
        }
        if (!strcmp(name, "hyn_sliderbar") && wheel_fd < 0)
            wheel_fd = fd;
        else if (!strcmp(name, "hyn_ts") && touch_fd < 0)
            touch_fd = fd;
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
    if (touch_fd >= 0 && n < max) fds[n++] = touch_fd;
    return n;
}

static int wheel_event(const struct input_event *e, input_t *out, int max) {
    int n = 0;
    if (e->type == EV_ABS && e->code == ABS_X) x = e->value;
    if ((e->type == EV_KEY && e->code == BTN_TOUCH) ||
        (e->type == EV_ABS && e->code == ABS_PRESSURE)) {
        int was = touching;
        touching = e->value != 0;
        if (!touching) have_last = 0, acc = 0;
        if (was && !touching && max > 0) {
            out[0] = (input_t){ IN_TUG, 0, 0, now_ms() };
            return 1;
        }
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
        out[n++] = (input_t){ IN_TICK, -dir, 0, now_ms() };
    }
    if (d && n < max) out[n++] = (input_t){ IN_TUG, 0, (float)-acc / WHEEL_TICK_UNITS, now_ms() };
    return n;
}

static int touch_event(const struct input_event *e, input_t *out, int max) {
    if (e->type == EV_ABS && e->code == ABS_X) ts.raw_x = e->value;
    if (e->type == EV_ABS && e->code == ABS_Y) ts.raw_y = e->value;
    if (e->type == EV_KEY && e->code == BTN_TOUCH) ts.down = e->value != 0;
    if (e->type != EV_SYN || max < 1) return 0;
    int x = ts.raw_y, y = SCREEN_H - 1 - ts.raw_x, was = ts.was;
    x = x < 0 ? 0 : x >= SCREEN_W ? SCREEN_W - 1 : x;
    y = y < 0 ? 0 : y >= SCREEN_H ? SCREEN_H - 1 : y;
    ts.was = ts.down;
    if (!ts.down && !was) return 0;
    if (!ts.down) x = ts.sent_x, y = ts.sent_y; /* lifting: where it last was */
    else if (was && x == ts.sent_x && y == ts.sent_y) return 0;
    ts.sent_x = x, ts.sent_y = y;
    out[0] = (input_t){ !was ? IN_TOUCH_DOWN : ts.down ? IN_TOUCH_MOVE : IN_TOUCH_UP, 0, 0, now_ms(), x, y };
    return 1;
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
        for (unsigned i = 0; i < got / sizeof *e && n < max; ++i) {
            if (e[i].type != EV_KEY || e[i].value == 2) continue;
            if (e[i].value) {
                down.code = e[i].code, down.at = now_ms(), down.held = 0;
                out[n++] = (input_t){ IN_KEY_DOWN, e[i].code, 0, down.at };
            } else {
                if (!(down.code == e[i].code && down.held))
                    out[n++] = (input_t){ IN_KEY_UP, e[i].code, 0, now_ms() };
                if (down.code == e[i].code) down.code = 0;
            }
        }
    }
    for (;;) {
        long got = touch_fd >= 0 ? read(touch_fd, e, sizeof e) : 0;
        if (got < (long)sizeof *e) break;
        for (unsigned i = 0; i < got / sizeof *e; ++i) n += touch_event(&e[i], out + n, max - n);
    }
    if (down.code && !down.held && now_ms() - down.at >= KEY_HOLD_MS && n < max) {
        down.held = 1;
        out[n++] = (input_t){ IN_KEY_HOLD, down.code, 0, now_ms() };
    }
    return n;
}

int input_wait_ms(void) {
    if (!down.code || down.held) return -1;
    unsigned gone = now_ms() - down.at;
    return gone >= KEY_HOLD_MS ? 0 : (int)(KEY_HOLD_MS - gone);
}

void input_flush(void) {
    struct input_event e[32];
    while (wheel_fd >= 0 && read(wheel_fd, e, sizeof e) >= (long)sizeof *e);
    while (keys_fd >= 0 && read(keys_fd, e, sizeof e) >= (long)sizeof *e);
    while (touch_fd >= 0 && read(touch_fd, e, sizeof e) >= (long)sizeof *e);
    touching = have_last = acc = 0;
    ts.down = ts.was = 0;
    down.code = 0;
}
