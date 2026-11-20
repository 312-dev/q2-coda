/* test_q2bt_sleep.py's q2bt.c: the device side built for this machine with its system calls played here.
 * The clock is this file's: a poll or usleep moves it on by its timeout, and each HCIGETDEVINFO by a
 * millisecond (so a loop that never sleeps still ends, its cost counted in calls). hci0 sends 30 kB a
 * second while it is up; hci_gone(1) takes it away as Linux does when the stack is stopped (the ioctl
 * fails with ENODEV). Every socket opens; nothing is ever read from one. */
#include <errno.h>
#include <string.h>
#include <sys/wait.h>
#include <poll.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>

#define BTPROTO_HCI 1
#define HCIGETDEVINFO 0x800448d3u
#define HCIGETCONNLIST 0x800448d4u

static unsigned clock_ms = 1000000, devinfo_calls;
static int gone;
static int fake_socket(int domain, int type, int proto) { return 900; }
static int fake_bind(int fd, const void *a, unsigned n) { return 0; }
static int fake_setsockopt(int fd, int l, int o, const void *v, unsigned n) { return 0; }
static int fake_poll(struct pollfd *p, unsigned long n, int ms) { return clock_ms += ms > 0 ? (unsigned)ms : 0, 0; }
static int fake_usleep(unsigned us) { return clock_ms += us / 1000, 0; }
static int fake_fork(void) { return -1; }
static int fake_ioctl(int fd, unsigned long req, void *arg) {
    if (req != HCIGETDEVINFO) return -1; /* no connection list: no handle, no raw link reads */
    ++devinfo_calls, ++clock_ms;
    if (gone) return errno = ENODEV, -1;
    unsigned char *di = arg;
    unsigned bytes = clock_ms * 30, mtu = 1021, pkts = 5;
    memcpy(di + 44, &mtu, 2), memcpy(di + 46, &pkts, 2), memcpy(di + 88, &bytes, 4);
    return 0;
}
#define socket fake_socket
#define bind fake_bind
#define setsockopt fake_setsockopt
#define poll fake_poll
#define usleep fake_usleep
#define fork fake_fork
#define ioctl fake_ioctl

#include "../../patch/q2bt.c"

/* q2guard.c's, which q2bt.c shares */
volatile int guard_woke;
unsigned now_ms(void) { return clock_ms; }
int slurp(const char *path, char *buf, int size) { return buf[0] = 0, -1; }
int read_int(const char *path, int *v) { return -1; }
int busy_since(struct cpu *c, int *cores) { return -1; }
int card_mounted(void) { return 0; }

static card_log heat_ring, ulog_ring;
void start(const char *dir) {
    static char a[256], b[256];
    snprintf(a, sizeof a, "%s/q2-bt.0", dir), snprintf(b, sizeof b, "%s/q2-log.0", dir);
    bt_init(card_log_at(&heat_ring, a, 65536), card_log_at(&ulog_ring, b, 65536));
}
void hci_gone(int g) { gone = g; }
/* One of q2guard's waits: how long it took on the clock and how many HCIGETDEVINFO it made. */
unsigned sleep_for(unsigned ms, unsigned *calls) {
    unsigned at = clock_ms, was = devinfo_calls;
    bt_sleep(ms);
    *calls = devinfo_calls - was;
    return clock_ms - at;
}
int streaming(void) { return bt_streaming(); }
const char *bt_ram(void) { return heat_ring.ring; }
int bt_used(void) { return (int)heat_ring.len; }
