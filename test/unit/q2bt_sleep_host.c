/* test_q2bt_sleep.py's and test_q2bt_recorder.py's q2bt.c: the device side built for this machine with its system
 * calls played here. The clock is this file's: a poll or usleep that finds nothing waiting moves it on by its
 * timeout, and each HCIGETDEVINFO by a millisecond (so a loop that never sleeps still ends, its cost counted in
 * calls). hci0 sends tx_rate bytes a second (30 kB, a stream, unless set) while it is up; hci_gone(1) takes it away
 * as Linux does when the stack is stopped (the ioctl fails with ENODEV). The connection list holds the links
 * link_add puts there. A raw HCI socket answers each read-only command the recorder sends with a Command Complete
 * (the opcodes are counted, refused or not); wpa_supplicant's control socket answers STATUS with wifi_freq's
 * frequency (0: no wpa_supplicant); and slurp serves the UART's counters, /proc/net/l2cap, wlan0's statistics and
 * the coex dump. Every other read or write goes to the real file. */
#include <errno.h>
#include <string.h>
#include <sys/wait.h>
#include <poll.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define BTPROTO_HCI 1
#define HCIGETDEVINFO 0x800448d3u
#define HCIGETCONNLIST 0x800448d4u

static unsigned clock_ms = 1000000, devinfo_calls, connlist_calls, tx_rate = 30000, rx_bytes;
static int gone;

/* ---- sockets: what each fake descriptor is ---- */
enum { FD_FREE, FD_HCI, FD_RAW, FD_MGMT, FD_UNIX, FD_WPA };
#define FAKE_FD 900
#define FAKE_FDS 64
static int kind[FAKE_FDS];
static unsigned char queue[FAKE_FDS][4096]; /* what a read of each returns, packets back to back */
static int qlen[FAKE_FDS], qpkt[FAKE_FDS][64], qn[FAKE_FDS];
static int sockets_made, raw_bound, unix_made;
static unsigned ops[0x10000];  /* HCI commands written, by opcode */
static int commands, wifi_freq, wpa_asked;
static int fake_of(int fd) { return fd >= FAKE_FD && fd < FAKE_FD + FAKE_FDS ? fd - FAKE_FD : -1; }
static void push(int f, const void *p, int n) {
    if (qn[f] >= 64 || qlen[f] + n > (int)sizeof queue[f]) return;
    memcpy(queue[f] + qlen[f], p, (size_t)n), qpkt[f][qn[f]++] = n, qlen[f] += n;
}
static int fake_socket(int domain, int type, int proto) {
    for (int f = 0; f < FAKE_FDS; ++f)
        if (kind[f] == FD_FREE) {
            kind[f] = domain == AF_UNIX ? FD_UNIX : FD_HCI, qlen[f] = qn[f] = 0;
            ++sockets_made, unix_made += domain == AF_UNIX;
            return FAKE_FD + f;
        }
    return errno = EMFILE, -1;
}
static int fake_bind(int fd, const void *a, unsigned n) {
    int f = fake_of(fd);
    const unsigned short *addr = a;
    if (f >= 0 && kind[f] == FD_HCI && n >= 6) {
        if (addr[2] == 0) kind[f] = FD_RAW, ++raw_bound; /* HCI_CHANNEL_RAW */
        if (addr[2] == 3) kind[f] = FD_MGMT;           /* HCI_CHANNEL_CONTROL */
    }
    return 0;
}
static int fake_setsockopt(int fd, int l, int o, const void *v, unsigned n) { return 0; }
static int fake_connect(int fd, const void *a, unsigned n) {
    int f = fake_of(fd);
    const char *path = (const char *)a + 2;
    if (f < 0 || kind[f] != FD_UNIX || !wifi_freq || !strstr(path, "wpa_supplicant/wlan0")) return errno = ENOENT, -1;
    kind[f] = FD_WPA;
    return 0;
}
static ssize_t fake_send(int fd, const void *b, size_t n, int flags) {
    int f = fake_of(fd);
    if (f < 0 || kind[f] != FD_WPA) return errno = ENOTCONN, -1;
    if (n == 6 && !memcmp(b, "STATUS", 6)) {
        char s[200];
        int k = snprintf(s, sizeof s, "bssid=a0:b1:c2:d3:e4:f5\nfreq=%d\nssid=home\nwpa_state=COMPLETED\n", wifi_freq);
        push(f, s, k), ++wpa_asked;
    }
    return (ssize_t)n;
}
/* A Command Complete for a read the recorder sends: status 0, the handle, a value */
static void answer(int f, int op, const unsigned char *c) {
    unsigned char e[32] = { 4, 0x0e, 0, 1, (unsigned char)op, (unsigned char)(op >> 8), 0, c[4], c[5] };
    int n = 9;
    switch (op) {
    case 0x1405: e[n++] = 0xf9; break;                     /* RSSI -7 */
    case 0x1406: e[n++] = 1, memset(e + n, 0xff, 6), memset(e + n + 6, 0, 4), n += 10; break; /* AFH: 48 channels */
    case 0x0809: e[n++] = 0; break;                        /* the Q2 central */
    case 0x0c36: e[n++] = 0x00, e[n++] = 0x7d; break;      /* 20 s */
    case 0x0c27: e[n++] = 0, e[n++] = 0; break;            /* none */
    case 0x0c2d: e[n++] = 4; break;                        /* 4 dBm */
    default: e[6] = 0x01; break;                           /* Unknown HCI Command */
    }
    e[2] = (unsigned char)(n - 3);
    push(f, e, n);
}
static ssize_t fake_write(int fd, const void *b, size_t n) {
    int f = fake_of(fd);
    if (f < 0) return write(fd, b, n);
    const unsigned char *c = b;
    if (kind[f] == FD_RAW && n >= 4 && c[0] == 1) {
        int op = c[1] | c[2] << 8;
        ++ops[op], ++commands;
        answer(f, op, c);
    }
    return (ssize_t)n;
}
static ssize_t fake_read(int fd, void *b, size_t n) {
    int f = fake_of(fd);
    if (f < 0) return read(fd, b, n);
    if (!qn[f]) return errno = EAGAIN, -1;
    int k = qpkt[f][0];
    memcpy(b, queue[f], (size_t)k < n ? (size_t)k : n);
    memmove(queue[f], queue[f] + k, (size_t)(qlen[f] - k)), qlen[f] -= k;
    memmove(qpkt[f], qpkt[f] + 1, (size_t)(qn[f] - 1) * sizeof qpkt[f][0]), --qn[f];
    return k;
}
static int fake_close(int fd) {
    int f = fake_of(fd);
    if (f < 0) return close(fd);
    kind[f] = FD_FREE, qlen[f] = qn[f] = 0;
    return 0;
}
static int fake_poll(struct pollfd *p, unsigned long n, int ms) {
    int ready = 0;
    for (unsigned long i = 0; i < n; ++i) {
        int f = fake_of(p[i].fd);
        p[i].revents = f >= 0 && qn[f] ? POLLIN : 0;
        ready += p[i].revents != 0;
    }
    if (!ready) clock_ms += ms > 0 ? (unsigned)ms : 0;
    return ready;
}
static int fake_usleep(unsigned us) { return clock_ms += us / 1000, 0; }
static int fake_fork(void) { return -1; }

/* ---- the controller's links ---- */
static struct {
    int handle;
    unsigned char addr[6];
} links[4];
static int nlinks;
static int fake_ioctl(int fd, unsigned long req, void *arg) {
    unsigned char *b = arg;
    if (req == HCIGETCONNLIST) {
        ++connlist_calls;
        if (gone) return errno = ENODEV, -1;
        int room = b[2] | b[3] << 8, k = 0;
        for (int i = 0; i < nlinks && k < room; ++i, ++k) {
            unsigned char *c = b + 4 + 16 * k;
            memset(c, 0, 16);
            c[0] = (unsigned char)links[i].handle, c[1] = (unsigned char)(links[i].handle >> 8);
            memcpy(c + 2, links[i].addr, 6), c[8] = 1; /* ACL_LINK */
        }
        b[2] = (unsigned char)k, b[3] = 0;
        return 0;
    }
    if (req != HCIGETDEVINFO) return -1;
    ++devinfo_calls, ++clock_ms;
    if (gone) return errno = ENODEV, -1;
    unsigned bytes = clock_ms / 1000 * tx_rate + clock_ms % 1000 * tx_rate / 1000, mtu = 1021, pkts = 5;
    memcpy(b + 44, &mtu, 2), memcpy(b + 46, &pkts, 2), memcpy(b + 88, &bytes, 4), memcpy(b + 84, &rx_bytes, 4);
    return 0;
}
#define socket fake_socket
#define bind fake_bind
#define setsockopt fake_setsockopt
#define connect fake_connect
#define send fake_send
#define write fake_write
#define read fake_read
#define close fake_close
#define poll fake_poll
#define usleep fake_usleep
#define fork fake_fork
#define ioctl fake_ioctl

#include "../../patch/q2bt.c"

/* ---- the files q2bt.c slurps ---- */
static unsigned uart_fe, wlan_rx;
static const char *coex_text;
static int files_on;
int slurp(const char *path, char *buf, int size) {
    buf[0] = 0;
    if (!files_on) return -1;
    if (!strcmp(path, BT_UART_PROC))
        return snprintf(buf, (size_t)size,
                        "serinfo:1.0 driver revision:\n0: uart:uart0 mmio:0x10030000 irq:55 tx:117194 rx:31613 fe:%u "
                        "RTS|CTS\n",
                        uart_fe);
    if (!strcmp(path, "/proc/net/l2cap"))
        return snprintf(buf, (size_t)size, "sk RefCnt Rmem Wmem User Inode Parent\n00000000 2 0 0 0 14021 0\n");
    if (!strcmp(path, WLAN "rx_bytes")) return snprintf(buf, (size_t)size, "%u\n", wlan_rx);
    if (!strcmp(path, WLAN "tx_bytes")) return snprintf(buf, (size_t)size, "1000\n");
    if (!strcmp(path, BT_COEX_PROC) && coex_text) return snprintf(buf, (size_t)size, "%s", coex_text);
    return -1;
}

/* q2guard.c's, which q2bt.c shares */
volatile int guard_woke;
unsigned now_ms(void) { return clock_ms; }
int read_int(const char *path, int *v) { return -1; }
int busy_since(struct cpu *c, int *cores) { return -1; }
int card_mounted(void) { return 0; }

static card_log heat_ring, ulog_ring;
void start(const char *dir) {
    static char a[256], b[256];
    snprintf(a, sizeof a, "%s/q2-bt.0", dir), snprintf(b, sizeof b, "%s/q2-log.0", dir);
    bt_init(card_log_at(&heat_ring, a, 1 << 20), card_log_at(&ulog_ring, b, 65536));
}
void hci_gone(int g) { gone = g; }
void set_tx_rate(unsigned bps) { tx_rate = bps; }
void link_add(int handle, const unsigned char *addr) {
    links[nlinks].handle = handle, memcpy(links[nlinks].addr, addr, 6), ++nlinks;
}
void links_clear(void) { nlinks = 0; }
void set_wifi(int freq) { wifi_freq = freq; }
void set_files(int on, const char *coex) { files_on = on, coex_text = coex; }
void uart_errors(unsigned fe) { uart_fe = fe; }
void wlan_rx_bytes(unsigned n) { wlan_rx = n; }
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
const char *ulog_ram(void) { return ulog_ring.ring; }
int ulog_used(void) { return (int)ulog_ring.len; }
unsigned op_count(int op) { return ops[op]; }
int command_count(void) { return commands; }
int raw_sockets(void) { return raw_bound; }
int unix_sockets(void) { return unix_made; }
int wpa_count(void) { return wpa_asked; }
unsigned connlists(void) { return connlist_calls; }
int raw_open_now(void) {
    for (int f = 0; f < FAKE_FDS; ++f)
        if (kind[f] == FD_RAW) return 1;
    return 0;
}
