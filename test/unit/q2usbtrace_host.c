/* test_q2usbtrace.py's device for patch/q2usbtrace.c: every t_* call the program makes is recorded (what, the path
 * or request, its flags) and answered from a simulated Q2 the test drives through the callbacks below. */
#include <string.h>

#include "../../patch/q2usbtrace.h"

struct call {
    char what[12];
    char path[96];
    long a, b, c;
};
struct call ut_calls[65536];
int ut_calls_cap = 65536;
int ut_ncalls;
static void rec(const char *what, const char *path, long a, long b, long c) {
    if (ut_ncalls >= (int)(sizeof ut_calls / sizeof ut_calls[0])) return;
    struct call *k = &ut_calls[ut_ncalls++];
    snprintf(k->what, sizeof k->what, "%s", what);
    snprintf(k->path, sizeof k->path, "%s", path ? path : "");
    k->a = a, k->b = b, k->c = c;
}

/* the simulated device: the clock, the register pages, the PMIC's registers, files and queues in Python */
long long ut_now = 1000000000LL, ut_slept;
unsigned ut_gpio[1024], ut_cpm[1024];
int ut_axp[256];
int ut_card_ready = 1, ut_line_busy, ut_debugfs;
char ut_ulogbuf[65536];
int ut_ulog_len;
int (*py_file)(const char *path, char *buf, int n);   /* a file's text: its length, or -1 for none */
int (*py_poll)(struct pollfd *p, int n, int timeout);  /* waits: advances ut_now, sets revents */
int (*py_line)(int line, struct ut_event_data *e);     /* a requested line's next edge: 1, or 0 none */
int (*py_recv)(int which, char *buf, int n);           /* 0 the forwarded edges, 1 the uevents: its length, or -1 */

enum { FD_CHIP = 10, FD_LINE = 20, FD_UDC = 30, FD_I2C = 31, FD_MEM = 32, FD_EDGES = 40, FD_UEV = 41, FD_FILE = 50 };
static char files[64][96];

int t_open(const char *path, int flags) {
    rec("open", path, flags, 0, 0);
    if (!strcmp(path, UT_GPIOCHIP)) return FD_CHIP;
    if (!strcmp(path, UT_UDC_STATE)) return FD_UDC;
    if (!strcmp(path, UT_I2C)) return FD_I2C;
    if (!strcmp(path, UT_MEM)) return FD_MEM;
    char b[8];
    if (py_file(path, b, sizeof b) < 0) return -1;
    for (int i = 0; i < 64; ++i)
        if (!files[i][0]) {
            snprintf(files[i], sizeof files[i], "%s", path);
            return FD_FILE + i;
        }
    return -1;
}
int t_close(int fd) {
    rec("close", "", fd, 0, 0);
    if (fd >= FD_FILE && fd < FD_FILE + 64) files[fd - FD_FILE][0] = 0;
    return 0;
}
int t_read(int fd, void *buf, int n) {
    if (fd >= FD_LINE && fd < FD_LINE + 3) {
        struct ut_event_data e;
        if (n < (int)sizeof e || !py_line(fd - FD_LINE + UT_PD17, &e)) return -1;
        memcpy(buf, &e, sizeof e);
        return sizeof e;
    }
    if (fd >= FD_FILE && fd < FD_FILE + 64) return py_file(files[fd - FD_FILE], buf, n);
    return -1;
}
int t_reread(int fd, void *buf, int n) { return fd == FD_UDC ? py_file(UT_UDC_STATE, buf, n) : -1; }
int t_ioctl(int fd, unsigned long req, void *arg) {
    if (req == UT_GPIO_GET_LINEEVENT) {
        struct ut_event_request *r = arg;
        rec("ioctl", "line", (long)req, r->lineoffset, (long)r->handleflags << 8 | r->eventflags);
        if (fd != FD_CHIP || ut_line_busy >> r->lineoffset & 1) return -1;
        r->fd = FD_LINE + (int)r->lineoffset - UT_PD17;
        ut_gpio[(UT_PORT_D + UT_INT) / 4] |= 1u << r->lineoffset; /* the kernel sets the pin's interrupt up */
        return 0;
    }
    if (req == UT_I2C_SMBUS) {
        struct ut_smbus_args *a = arg;
        rec("ioctl", "smbus", (long)req, a->command, (long)a->read_write << 8 | a->size);
        if (ut_axp[a->command] < 0) return -1;
        a->data[0] = (unsigned char)ut_axp[a->command];
        return 0;
    }
    rec("ioctl", "other", (long)req, (long)arg, 0);
    return fd == FD_I2C && req == UT_I2C_SLAVE_FORCE ? 0 : -1;
}
int t_poll(struct pollfd *p, int n, int timeout) {
    rec("poll", "", n, timeout, 0);
    return py_poll(p, n, timeout);
}
void *t_mmap(unsigned len, int fd, unsigned long off) {
    rec("mmap", "", len, fd, (long)off);
    if (fd != FD_MEM) return 0;
    return off == UT_GPIO_PAGE ? (void *)ut_gpio : off == UT_CPM_PAGE ? (void *)ut_cpm : 0;
}
int t_socket(int domain, int type, int protocol) {
    rec("socket", "", domain, type, protocol);
    return domain == UT_AF_NETLINK ? FD_UEV : FD_EDGES;
}
int t_bind(int fd, const void *addr, unsigned len) {
    char path[96] = "";
    if (fd == FD_EDGES && len > 3) memcpy(path, (const char *)addr + 3, len - 3 < 95 ? len - 3 : 95);
    rec("bind", path, fd, len, ((const unsigned short *)addr)[0]);
    return 0;
}
int t_recv(int fd, void *buf, int n) { return py_recv(fd == FD_UEV, buf, n); }
int t_mount_debugfs(void) {
    rec("mount", UT_DEBUGFS, 0, 0, 0);
    ut_debugfs = 1;
    return 0;
}
long long t_now(int clock) { return clock == CLOCK_BOOTTIME ? ut_now + ut_slept : ut_now; }
long t_wall(void) { return 1795400000; }
int t_exists(const char *path) {
    char b[8];
    return py_file(path, b, sizeof b) >= 0;
}
int t_append(const char *path, const char *s, int n) {
    rec("append", path, n, 0, 0);
    if (strcmp(path, UT_ULOG) || ut_ulog_len + n >= (int)sizeof ut_ulogbuf) return -1;
    memcpy(ut_ulogbuf + ut_ulog_len, s, n);
    ut_ulog_len += n;
    ut_ulogbuf[ut_ulog_len] = 0;
    return n;
}
int t_card_ready(void) { return ut_card_ready; }
