/* The USB cable's plug and unplug as they happen (docs/internals.md#usb-cable-events). Stock reads the
 * cable from GPIO PD17 (usb_detect 0x4ff220, low while VBUS is in) only from get_battery_capacity,
 * which the status bar's one-second timer calls, and acts on it in check_usb_status, which
 * on_wm_idle_status runs only when time() has ticked to a new second: a plug-in waited up to two
 * seconds before anything saw it. Here a thread waits on PD17's edges through the kernel's GPIO
 * character device (a line event request on gpiochip3, GPD, line 17, both edges) and counts each
 * settled burst of them; usb_ui_poll, every UI pass, sees the count change and runs stock's read
 * and check at once (usb_event_take). Stock's own once-a-second read stays, so a failed request
 * leaves the old behaviour. Each count also ends the UI loop's idle (loop_wake), so a plug-in is
 * seen on the pass it starts, dark or lit. */
#include "navigation.h"

#define USB_GPIOCHIP "/dev/gpiochip3" /* GPD: base 96, 32 lines (/sys/class/gpio/gpiochip96) */
#define USB_DET_LINE 17                /* PD17, stock's usb_detect */
#define USB_SETTLE_MS 20               /* a plug's contacts bounce for a few ms */
/* linux/gpio.h: GPIO_GET_LINEEVENT_IOCTL, _IOWR(0xB4, 0x04, struct gpioevent_request) (48 bytes),
 * the same number on MIPS as on x86 */
#define GPIO_GET_LINEEVENT_IOCTL 0xc030b404u
#define GPIOHANDLE_REQUEST_INPUT 1u
#define GPIOEVENT_REQUEST_BOTH_EDGES 3u

struct gpioevent_request {
    unsigned lineoffset, handleflags, eventflags;
    char consumer_label[32];
    int fd;
};
struct gpioevent_data { /* 16 bytes: the u64 aligns the struct */
    unsigned long long timestamp; /* CLOCK_MONOTONIC ns since Linux 5.7 */
    unsigned id, pad;
};

static struct {
    int state; /* 0 not started, 1 the thread waits on edges, -1 no events: stock's poll alone */
    int fd;
    volatile unsigned seq;                /* settled bursts of edges, the thread's count */
    volatile unsigned long long edge_ns; /* the first edge of the last burst */
    unsigned seen;                        /* the UI thread's last seq */
} ue __attribute__((section(".scratch")));

unsigned long long usb_event_now_ns(void) {
    struct {
        long s, ns;
    } t;
    clock_gettime(1 /* CLOCK_MONOTONIC */, (void *)&t);
    return (unsigned long long)t.s * 1000000000ull + (unsigned long long)t.ns;
}

#if Q2POD_DEV
/* --dev only: each edge read is sent on as it is (its kernel time and direction) to q2-usbtrace
 * (docs/internals.md#usb-tracer), which cannot ask for this line while this file holds it. A datagram
 * to an abstract name nobody has bound fails at once (ECONNREFUSED) and raises no signal, and the
 * socket never blocks, so the tracer's absence costs this thread one failed call an edge. */
#define USB_TRACE_NAME "q2-usbtrace-pd17" /* q2usbtrace.h UT_EDGES_NAME */
static int usb_trace_fd __attribute__((section(".scratch"))); /* the socket + 1; 0 not made yet */
static void usb_event_forward(const struct gpioevent_data *e) {
    if (!usb_trace_fd)
        usb_trace_fd = socket(1 /* AF_UNIX */, 1 /* SOCK_DGRAM on MIPS */ | 0x80000 /* SOCK_CLOEXEC */ |
                                                   0x80 /* SOCK_NONBLOCK */,
                              0) + 1;
    if (usb_trace_fd <= 0) return;
    struct {
        unsigned short family;
        char path[sizeof USB_TRACE_NAME + 1];
    } a = { 1, "\0" USB_TRACE_NAME };
    sendto(usb_trace_fd - 1, e, sizeof *e, 0x40 /* MSG_DONTWAIT */ | 0x4000 /* MSG_NOSIGNAL */, &a,
           2 + sizeof a.path - 1 /* the abstract name, its NUL left out */);
}
#else
#define usb_event_forward(e) ((void)0)
#endif

/* One burst: the edge read (blocking), the contacts' bounce let pass, its other edges drained. 0
 * when the line's file is gone. */
int usb_event_wait(int fd) {
    struct gpioevent_data e;
    if (read(fd, &e, sizeof e) != (int)sizeof e) return 0;
    usb_event_forward(&e);
    unsigned long long first = e.timestamp;
    usleep(USB_SETTLE_MS * 1000);
    fcntl(fd, 4 /* F_SETFL */, 0x80 /* O_NONBLOCK */);
    while (read(fd, &e, sizeof e) == (int)sizeof e) {
        usb_event_forward(&e);
    }
    fcntl(fd, 4 /* F_SETFL */, 0);
    ue.edge_ns = first;
    __sync_synchronize(); /* edge_ns before the count the UI thread reads */
    ++ue.seq;
    loop_wake(); /* the UI loop's idle ends: usb_ui_poll takes the count now (loop_wake.c) */
    return 1;
}

static void *usb_event_worker(void *unused) {
    while (usb_event_wait(ue.fd)) {
    }
    ue.state = -1; /* stock's poll alone from here */
    return 0;
}

/* Once, from the first USB pass: the line's edges requested and the thread started. */
void usb_event_start(void) {
    if (ue.state) return;
    ue.state = -1;
    int chip = open(USB_GPIOCHIP, 0 /* O_RDONLY */);
    if (chip < 0) return;
    struct gpioevent_request r = { USB_DET_LINE, GPIOHANDLE_REQUEST_INPUT, GPIOEVENT_REQUEST_BOTH_EDGES,
                                   "q2-usb-cable", -1 };
    int ok = !ioctl(chip, GPIO_GET_LINEEVENT_IOCTL, &r) && r.fd >= 0;
    close(chip);
    if (!ok) return;
    ue.fd = r.fd;
    unsigned long t;
    if (pthread_create(&t, 0, usb_event_worker, 0)) {
        close(r.fd);
        return;
    }
    pthread_detach(t);
    ue.state = 1;
}

/* The UI thread's look: 1 with the burst's first edge in *edge_ns when the cable has moved since
 * the last look. */
int usb_event_take(unsigned long long *edge_ns) {
    unsigned seq = ue.seq;
    if (seq == ue.seen) return 0;
    ue.seen = seq;
    __sync_synchronize();
    *edge_ns = ue.edge_ns;
    return 1;
}

int usb_event_live(void) { return ue.state == 1; }
